import badge
import gc
import urequests
import ugfx
import json
import sys
import utime

UNIX_EPOCH_OFFSET = 0
try:
    import wifi
    import machine
    import micropython
    rtc = machine.RTC()
    UNIX_EPOCH_OFFSET = 946684800  # seconds between Unix Epoch 1 Jan 1970 & MP embedded Epoch 1 Jan 2000
except ImportError:
    rtc = None
    wifi = None
    micropython = None

# Some layout dimensions
WIDTH = 296
HEIGHT = 128

LINE_Y = 33  # line between NumberDisplay and GraphDisplay
LINE_X = 20  # Y axis vertical line

XAXIS_Y = HEIGHT - 13  # X axis horizontal line


def round_up(value, to_next):
    result = ((int(value) + to_next - 1) // to_next) * to_next
    return result


def unix_time():
    return utime.time() + UNIX_EPOCH_OFFSET


class Sample(object):
    def __init__(self, ts, min_solar, max_solar, min_usage, max_usage):
        self.ts = int(ts)
        self.solar = None if min_solar is None else (min_solar, max_solar)
        self.usage = None if min_usage is None else (min_usage, max_usage)

    def __repr__(self):
        return '{}: {} / {}'.format(self.ts, self.solar, self.usage)

    def is_empty(self):
        return self.solar is None and (self.usage is None or self.usage == (0, 0))

    def max_power(self):
        return max(self.solar[1] if self.solar else 0, self.usage[1] if self.usage else 0)

    def update_time(self):
        # Lazy global timekeeping: Maintain the RTC clock from InfluxDB results,
        # assuming that the latest sample we get back should be less than 10 seconds
        # in the past (provided that samples are being updated...)
        if rtc:
            unix_ts = self.ts - UNIX_EPOCH_OFFSET
            if unix_ts > utime.time() + 5:  # to save on overhead, need to be adjusting by more than 5 seconds
                rtc.init(utime.gmtime(unix_ts))
                print('New time is {} {}'.format(unix_time(), utime.gmtime(int(utime.time()))))


class NumberDisplay(object):
    """
    Top half of display shows the current power generation and usage as numbers

    Draws to the display from (0,0) to (WIDTH-1, LINE_Y), inclusive
    """
    REDRAW_UPDATES = 50

    def __init__(self):
        self.num_updates = 0
        self.last_sample = None
        self.redraw_display()

    def redraw_display(self):
        for _ in range(3 if self.num_updates == self.REDRAW_UPDATES else 1):
            ugfx.area(0, 0, WIDTH, LINE_Y, ugfx.BLACK)
            ugfx.flush()
            ugfx.area(0, 0, WIDTH, LINE_Y, ugfx.WHITE)
            ugfx.flush()

        # line under the display, icons
        ugfx.line(0, LINE_Y, WIDTH - 1, LINE_Y, ugfx.BLACK)
        if sys.platform == 'linux':
            path = '.'
        else:
            path = '/apps/solar_usage'

        ugfx.display_image(0, 0, '{}/sun.png'.format(path))
        ugfx.display_image(130, 0, '{}/house.png'.format(path))
        ugfx.flush()

    def update(self, sample):
        self.last_sample = sample

        def as_text(value):
            return '  -' if value is None else '{:.1f}W'.format((value[0] + value[1])/2)

        if self.num_updates == self.REDRAW_UPDATES:
            self.redraw_display()  # do a full refresh
            self.num_updates = 0
        else:
            self.num_updates += 1

        self._draw(as_text(sample.solar), as_text(sample.usage))

    def update_no_sample(self):
        self._draw('???', '???')

    def _draw(self, solar_text, usage_text):
        ugfx.area(36, 4, 130-36, 32-4, ugfx.WHITE)
        ugfx.area(166, 4, WIDTH-166, 32-4, ugfx.WHITE)
        ugfx.string(36, 4, solar_text, 'Roboto_Regular22', ugfx.BLACK)
        ugfx.string(166, 4, usage_text, 'Roboto_Regular22', ugfx.BLACK)
        ugfx.flush()


class Graph(object):
    """
    Bottom half of display shows a historical data graph
    """
    X_WIDTH = WIDTH-LINE_X
    Y_HEIGHT = XAXIS_Y-LINE_Y-2
    UPDATES_FULL_REFRESH = 3

    WIDTH_SECONDS = 60 * 60  # width of graph in seconds
    SECONDS_PER_PIXEL = int(WIDTH_SECONDS / X_WIDTH)  # this many seconds per pixel (float)

    def __init__(self):
        self.num_updates = 0
        self.last_x = None
        self.last_usage_y = None
        self.samples = []
        self.max_power = None
        self.origin_ts = None  # timestamp that correlates to the left-hand side of the graph, update() will recalc it

    def timestamp_to_x(self, ts):
        r = ((ts - self.origin_ts) * self.X_WIDTH) // self.WIDTH_SECONDS
        r = max(0, min(r, self.X_WIDTH - 1))
        return LINE_X + r

    def value_to_y(self, value):
        if value < 0:
            value = 0  # some kind of influx bug causes occasional negative minimums?
        assert value < self.max_power
        result = (value / self.max_power) * self.Y_HEIGHT
        result = max(result, 1)
        return self.Y_HEIGHT - int(result) + LINE_Y

    def redraw_display(self):
        # draw the X & Y axis lines
        self.num_updates += 1
        if self.num_updates == self.UPDATES_FULL_REFRESH:
            # cycle the eink display to refresh the pixels
            self.num_updates = 0
            for _ in range(3):
                ugfx.area(0, LINE_Y+1, WIDTH, HEIGHT-LINE_Y-1, ugfx.BLACK)
                ugfx.flush()
                ugfx.area(0, LINE_Y+1, WIDTH, HEIGHT-LINE_Y-1, ugfx.WHITE)
                ugfx.flush()
        else:
            # in between, just draw the display white one time
            ugfx.area(0, LINE_Y+1, WIDTH, HEIGHT-LINE_Y-1, ugfx.WHITE)
            ugfx.flush()

        ugfx.line(LINE_X, LINE_Y, LINE_X, XAXIS_Y, ugfx.BLACK)
        ugfx.line(0, XAXIS_Y, WIDTH-1, XAXIS_Y, ugfx.BLACK)
        ugfx.flush()

        self.redraw_y_axis()
        self.redraw_x_axis()

        self.last_x = None
        self.last_usage_y = None

        if not self.samples:
            self.origin_ts = None
            return  # nothing else to draw, leave the X axis and graph area blank

        self.draw_samples(self.samples)  # draw the full graph!

    def redraw_y_axis(self):
        steps = 6
        watts_per_step = self.max_power / steps
        for step in range(steps):
            y = self.value_to_y(step * watts_per_step)
            if step % 2 == 1:
                from_x = LINE_X - 2
                ugfx.string(0, y - 6, '{:.1f}'.format(step * watts_per_step / 1000), 'Roboto_Regular12', ugfx.BLACK)
            else:
                from_x = LINE_X - 5
            ugfx.line(from_x, y, LINE_X, y, HEIGHT-1)
        ugfx.flush()

    def redraw_x_axis(self):
        if self.origin_ts is None:
            return  # no X axis yet, will draw it as soon as we get a sample
        NUM_SEGMENTS = 4
        ts = self.origin_ts
        for m in range(NUM_SEGMENTS + 1):
            x = self.timestamp_to_x(ts)
            ugfx.line(x, XAXIS_Y, x, HEIGHT - 4, ugfx.BLACK)
            minutes = (ts // 60) % 60
            ugfx.string(x + 2, XAXIS_Y, ':{:02}'.format(minutes), 'Roboto_Regular12', ugfx.BLACK)
            ugfx.flush()
            ts += self.WIDTH_SECONDS // NUM_SEGMENTS

    def update(self, samples):
        # check for a scroll event
        SCROLL_SECONDS = self.WIDTH_SECONDS // 4
        new_origin = round_up(unix_time(), SCROLL_SECONDS) - self.WIDTH_SECONDS
        if new_origin != self.origin_ts:
            print('graph timestamp range {} - {} ({} seconds)'.format(
                new_origin, new_origin + self.WIDTH_SECONDS, self.WIDTH_SECONDS))
        while self.samples and self.samples[0].ts < new_origin:
            del self.samples[0]

        for new_sample in samples:
            # add the new samples to the current list of samples
            if new_sample.ts < new_origin:
                continue
            elif self.samples and self.samples[-1].ts >= new_sample.ts:
                self.samples[-1] = new_sample  # some new samples are dups
            else:
                self.samples.append(new_sample)

        if not self.samples:
            return  # empty

        new_max = round_up(max(s.max_power() for s in self.samples), 500)
        new_max += 500
        if new_origin != self.origin_ts or new_max != self.max_power:
            print('origin {} -> {} max {} -> {}, redraw!'.format(
                self.origin_ts, new_origin, self.max_power, new_max))
            # need to draw the whole graph again!
            self.origin_ts = new_origin
            self.max_power = new_max
            self.redraw_display()
        else:
            # just draw the new samples here, onto the existing graph
            self.draw_samples(samples)

    def draw_samples(self, samples):
        print('Drawing {} samples'.format(len(samples)))

        for s in samples:
            x = self.timestamp_to_x(s.ts)

            if s.solar:
                solar_y_min = self.value_to_y(s.solar[0])
                solar_y_max = self.value_to_y(s.solar[1])
                solar_x = x - (x % 2)  # no greyscale, so draw the solar as a dotted line,
                ugfx.line(solar_x, solar_y_min, solar_x, solar_y_max, ugfx.BLACK)
            if s.usage:
                usage_y_min = self.value_to_y(s.usage[0])
                usage_y_max = self.value_to_y(s.usage[1])
                ugfx.line(x, usage_y_min, x, usage_y_max, ugfx.BLACK)
                # horizontally join the high points of the usage graph, if they exist
                if self.last_x in (x - 1, x - 2):
                    ugfx.line(self.last_x, self.last_usage_y, x, usage_y_max, ugfx.BLACK)
                self.last_x = x
                self.last_usage_y = usage_y_max
        ugfx.flush()


def uri_encode(seq):
    resp = b''
    for c in seq.encode():
        if (ord(b'A') <= c <= ord('Z')) or (ord(b'a') <= c <= ord(b'z')) \
           or (ord(b'0') <= c <= ord(b'9')) or c in b'-_.~':
            resp += chr(c)
        else:
            resp += b'%{:02x}'.format(c)
    return resp


def main():
    badge.init()
    ugfx.init()
    if wifi:
        wifi.connect()

    influxdb_url = badge.nvs_get_str('solar_usage', 'influxdb_url')
    print('InfluxDB URL: {}'.format(influxdb_url))

    ugfx.clear(ugfx.BLACK)
    ugfx.flush()
    ugfx.clear(ugfx.WHITE)
    ugfx.string(WIDTH//2 - 50, HEIGHT//2 - 11, 'Solarising...', 'Roboto_Regular22', ugfx.BLACK)
    ugfx.flush()

    samples = []
    start = 'now() - {}s'.format(Graph.WIDTH_SECONDS)
    while not samples:
        samples = query_data(influxdb_url, start)
        print("got {} initial samples".format(len(samples)))

    last_sample = samples[0]
    samples[-1].update_time()

    numbers = NumberDisplay()
    graph = Graph()
    while True:
        if samples:
            if samples[-1].ts > last_sample.ts:
                numbers.update(samples[-1])
            elif unix_time() - last_sample.ts > 30:
                numbers.update_no_sample()
            graph.update(samples)

            last_sample = samples[-1]
            last_sample.update_time()

        utime.sleep(5)
        samples = query_data(influxdb_url, last_sample.ts)
        print('got {} samples'.format(len(samples)))


def query_data(influxdb_url, since):
    # returns list of 3-lists [timestamp, solar, load]

    if isinstance(since, int):
        since = '{}s'.format(since)

    result = []

    query = uri_encode('SELECT min(solar),max(solar),max(load)*-1,min(load)*-1 from power where '
                       'time > {} group by time({}s) fill(none)'.format(since, Graph.SECONDS_PER_PIXEL))

    try:
        resp = urequests.post('{}/query?db=sensors&epoch=s'.format(influxdb_url),
                              data=b'q='+query,
                              headers={
                                  'Content-Type': 'application/x-www-form-urlencoded'
                              })
    except OSError:
        print("Failed to connect to InfluxDB server")
        return []

    if resp.status_code != 200:
        print("InfluxDB returned error code {}".format(resp.status_code))
        resp.close()
        return []

    data = json.load(resp.raw)
    resp.close()
    gc.collect()
    result = [Sample(*x) for x in data['results'][0]['series'][0]['values']]
    result = [s for s in result if not s.is_empty()]   # remove all the empty samples
    return result


if __name__ == '__main__':
    main()
