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


class Sample(object):
    def __init__(self, ts, solar, usage):
        self.ts = int(ts)
        self.solar = solar
        self.usage = usage

    def __repr__(self):
        return "{}: {} / {}".format(self.ts, self.solar, self.usage)

    def is_empty(self):
        return self.solar is None and self.usage is None

    def max_power(self):
        return max(self.solar or 0, self.usage or 0)

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
        for _ in range(1):  # todo make this cycle to refresh e-ink
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


class Graph(object):
    """
    Bottom half of display shows a historical data graph

    Draws to the display from (0,0) to (WIDTH-1, LINE_Y), inclusive
    """
    TS_SCALAR = 15  # this many seconds per pixel
    X_WIDTH = WIDTH-LINE_X  # 278, 1 minute per pixel
    Y_HEIGHT = XAXIS_Y-LINE_Y

    def __init__(self):
        self.num_updates = 0  # trigger a full redraw next
        self.samples = []
        self.max_power = 5000  # determines the scale of the graph in Watts, update() will recalc it
        self.origin_ts = None  # timestamp that correlates to the left-hand side of the graph, update() will recalc it
        self.last_x = None
        self.last_usage_y = None
        self.last_solar_y = None
        self.redraw_display()

    def redraw_display(self):
        # draw the X & Y axis lines

        # todo: do the binky thing here too
        ugfx.area(0, LINE_Y, WIDTH, HEIGHT-LINE_Y, ugfx.WHITE)
        ugfx.line(LINE_X, LINE_Y, LINE_X, XAXIS_Y, ugfx.BLACK)
        ugfx.line(0, XAXIS_Y, WIDTH-1, XAXIS_Y, ugfx.BLACK)
        ugfx.flush()

        # draw the graph Y axis
        step_height = 15
        steps = 6
        watts_per_step = self.max_power // steps
        for step in range(steps):
            y = XAXIS_Y - step * step_height
            if step % 2 == 1:
                from_x = LINE_X-4
                # to_x = LINE_X
                ugfx.string(0, y - 6, "{:.1f}".format(step * watts_per_step / 1000), "", ugfx.BLACK)
            else:
                from_x = LINE_X - 3
                # to_x = LINE_X - 1
            ugfx.line(from_x, y, LINE_X, y, HEIGHT-1)
        ugfx.flush()

        if not self.samples:
            self.origin_ts = None
            return  # nothing else to draw, leave the X axis and graph area blank

        self.last_x = None
        self.last_solar_y = None
        self.last_usage_y = None
        self.draw_samples(self.samples)  # draw the full graph!

    def update(self, samples):
        for new_sample in samples:
            # add the new samples to the current list of samples
            # (assuming they come in order, but possibly some new samples are dups)
            changed = False
            if not self.samples or self.samples[-1].ts < new_sample.ts:
                self.samples.append(new_sample)
                changed = True
        if not changed:
            return  # nothing new, nothing to do

        WIDTH_SECONDS = (self.X_WIDTH * self.TS_SCALAR)
        SCROLL_SECONDS = WIDTH_SECONDS // 4
        new_origin = ((self.samples[-1].ts + 1) // SCROLL_SECONDS) * SCROLL_SECONDS \
            - 3 * SCROLL_SECONDS
        print("graph timestamp range {} - {} ({} seconds)".format(
            new_origin, new_origin + WIDTH_SECONDS, WIDTH_SECONDS))
        while self.samples[0].ts < new_origin:
            del self.samples[0]

        # there should be at least one sample in the graph at this point (or above will error out)

        new_max = int(max(s.max_power() for s in self.samples) * 1.05)  # 5% headroom on graph
        if new_origin != self.origin_ts or new_max != self.max_power:
            print("origin {} -> {} max {} -> {}, redraw!".format(
                self.origin_ts, new_origin, self.max_power, new_max))
            # need to draw the whole graph again!
            self.origin_ts = new_origin
            self.max_power = new_max
            self.redraw_display()
        else:
            # just draw the new samples here, onto the existing graph
            self.draw_samples(samples)

    def draw_samples(self, samples):
        print("Drawing {} samples".format(len(samples)))

        def value_to_y(value):
            assert value < self.max_power
            result = (value / self.max_power) * self.Y_HEIGHT
            return self.Y_HEIGHT - int(result) + LINE_Y

        for s in samples:
            x = (s.ts - self.origin_ts) // self.TS_SCALAR + LINE_X
            if self.last_x is None:
                self.last_x = x

            solar_y = None
            usage_y = None
            if s.solar:
                solar_y = value_to_y(s.solar)
                if self.last_solar_y is None:
                    self.last_solar_y = solar_Y
                ugfx.line(self.last_x, self.solar_y, x, solar_y, ugfx.GREY)  # color??
                self.last_solar_y = solar_y
            if s.usage:
                usage_y = value_to_y(s.usage)
                if self.last_usage_y is None:
                    self.last_usage_y = usage_y
                ugfx.line(self.last_x, self.last_usage_y, x, usage_y, ugfx.BLACK)
                self.last_usage_y = usage_y
            self.last_x = x
            print(s,x,solar_y,usage_y)
        ugfx.flush()

    def sample_y(self, value):
        return result


def uri_encode(seq):
    resp = b''
    for c in seq.encode():
        if (ord(b'A') <= c <= ord('Z')) or (ord(b'a') <= c <= ord(b'z')) \
           or (ord(b'0') <= c <= ord(b'9')) or c in b'-_.~':
            resp += chr(c)
        else:
            resp += b"%{:02x}".format(c)
    return resp


def main():
    ugfx.init()

    numbers = NumberDisplay()
    graph = Graph()

    samples = []
    while not samples:
        samples = query_data('now() - 60m')
        print(samples)

    while True:
        print("in main loop")
        if samples:
            numbers.update(samples[-1])
            graph.update(samples)
        time.sleep(5)
        samples = query_data('{}s'.format(samples[-1].ts), '5s')
        print("got {} samples".format(len(samples)))


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
