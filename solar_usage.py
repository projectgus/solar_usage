import urequests
import ugfx
import time
import json

# Some layout dimensions
WIDTH = 296
HEIGHT = 128

LINE_Y = 33  # line between NumberDisplay and GraphDisplay
LINE_X = 18  # Y axis vertical line

XAXIS_Y = HEIGHT-14  # X axis horizontal line

X_WIDTH = WIDTH-LINE_X  # 278, 1 minute per pixel
Y_HEIGHT = XAXIS_Y-LINE_Y


class Sample(object):
    def __init__(self, ts, solar, usage):
        self.ts = ts
        self.solar = solar
        self.usage = usage

    def __repr__(self):
        return "{}: {} / {}".format(self.ts, self.solar, self.usage)

    def is_empty(self):
        return self.solar is None and self.usage is None

    def close_to(self, b):
        def close_values(a, b):
            return abs((a or 0) - (b or 0)) < 0.2

        if b is None:
            return False
        return close_values(self.solar, b.solar) and close_values(self.usage, b.usage)


class NumberDisplay(object):
    """
    Top half of display shows the current power generation and usage as numbers

    Draws to the display from (0,0) to (WIDTH-1, LINE_Y), inclusive
    """
    REDRAW_UPDATES = 3

    def __init__(self):
        self.num_updates = 0  # trigger a full redraw next
        self.last_sample = None
        self.redraw_display()

    def redraw_display(self):
        for _ in range(3):
            # cycle the whole region to refresh the e-ink pixels
            ugfx.area(0, 0, WIDTH, LINE_Y, ugfx.BLACK)
            ugfx.flush()
            ugfx.area(0, 0, WIDTH, LINE_Y, ugfx.WHITE)
            ugfx.flush()

        # line under the display, icons
        ugfx.line(0, LINE_Y, WIDTH - 1, LINE_Y, ugfx.BLACK)
        ugfx.Imagebox(0, 0, 32, 32, './sun.png')
        ugfx.Imagebox(130, 0, 32, 32, './house.png')
        ugfx.flush()

    def update(self, sample):
        if sample.close_to(self.last_sample):
            return  # if not much changed, don't bother updating this part
        self.last_sample = sample

        if self.num_updates == self.REDRAW_UPDATES:
            self.redraw_display()  # do a full refresh
            self.num_updates = 0
        else:
            self.num_updates += 1

        def as_text(value):
            return "-" if value is None else "{:.1f}W".format(value)

        ugfx.area(36, 4, 130-36, 32-4, ugfx.WHITE)
        ugfx.area(166, 4, WIDTH-166, 32-4, ugfx.WHITE)
        ugfx.string(36, 4, as_text(sample.solar), 'Roboto_Regular22', ugfx.BLACK)
        ugfx.string(166, 4, as_text(sample.usage), 'Roboto_Regular22', ugfx.BLACK)
        ugfx.flush()


def uri_encode(seq):
    resp = b''
    for c in seq.encode():
        if (ord(b'A') <= c <= ord('Z')) or (ord(b'a') <= c <= ord(b'z')) \
           or (ord(b'0') <= c <= ord(b'9')) or c in b'-_.~':
            resp += chr(c)
        else:
            resp += b"%{:02x}".format(c)
    return resp


def redraw_display():
    ugfx.line(LINE_X, LINE_Y, LINE_X, XAXIS_Y, ugfx.BLACK)
    ugfx.line(0, XAXIS_Y, WIDTH-1, XAXIS_Y, ugfx.BLACK)

    ugfx.flush()

    # draw the graph Y axis
    step_height = 15
    steps = 6
    kw_per_step = 1
    for step in range(steps):
        y = XAXIS_Y - step * step_height
        if step % 2 == 1:
            from_x = LINE_X-4
            to_x = LINE_X
            ugfx.string(0, y - 6, "{:.1f}".format(step * kw_per_step), "", ugfx.BLACK)
        else:
            from_x = LINE_X - 3
            to_x = LINE_X - 1
        ugfx.line(from_x, y, LINE_X, y, HEIGHT-1)
    ugfx.flush()


def draw_graph(samples):
    max_power = get_max_power(samples)

    # draw all points
    last_p = samples[0].usage or 0
    last_x = LINE_X
    last_y = int(Y_HEIGHT - (last_p / max_power) * Y_HEIGHT)
    TS_SCALAR = 15  # this many seconds per pixel
    ORIGIN_TS = (samples[-1].ts // X_WIDTH) * X_WIDTH // TS_SCALAR
    print("timestamps from {} to {}".format(samples[0].ts, samples[-1].ts))
    print("graph range from {} to {}".format(ORIGIN_TS, ORIGIN_TS + X_WIDTH))
    print(X_WIDTH)
    for s in samples:
        ts = s.ts / TS_SCALAR
        if s.ts < ORIGIN_TS:
            continue
        x = ts - ORIGIN_TS + LINE_X
        if s.usage is None:
            continue
        y = int(Y_HEIGHT - (s.usage / max_power) * Y_HEIGHT)
        ugfx.line(last_x, last_y, x, y, ugfx.BLACK)
        last_x = x
        last_y = y


def main():
    ugfx.init()

    numbers = NumberDisplay()

    redraw_display()

    r = query_data('now() - 60m')
    print(r)

    if len(r):
        numbers.update(r[-1])

    draw_graph(r)

    while True:
        print("in main loop")
        time.sleep(5)
        print(r[-1])
        print("sampling since {}".format(r[-1].ts))
        return
        r = query_data('{}s'.format(r[-1].ts), '5s')
        if r:
            latest = r[-1]
            print(latest.ts, latest.solar, latest.usage)
            numbers.update(latest)


def get_max_power(samples):
    try:
        max_solar = max(s.solar for s in samples if s.solar)
    except ValueError:
        max_solar = None
    try:
        max_load = max(s.usage for s in samples if s.usage)
    except ValueError:
        max_load = None
    try:
        max_power = max(max_solar, max_load)
    except TypeError:
        max_power = max_solar or max_load
    return max_power


def query_data(since, group_by='5s'):
    # returns list of 3-lists [timestamp, solar, load]
    query = uri_encode('SELECT mean(solar),mean(load)*-1 from power where '
                       'time > {} group by time({})'.format(since, group_by))

    # spiky.lan
    resp = urequests.post('http://192.168.66.1:8086/query?db=sensors&epoch=s',
                          data=b'q='+query,
                          headers={
                              'Content-Type': 'application/x-www-form-urlencoded'
                          })
    text = resp.text
    resp.close()

    if not len(text):
        return []
    data = json.loads(text)
    result = [Sample(*x) for x in data['results'][0]['series'][0]['values']]
    result = [s for s in result if not s.is_empty()]   # remove all the empty samples
    return result


if __name__ == "__main__":
    main()
