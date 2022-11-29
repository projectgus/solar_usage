# solar_usage monitor

A quick personal project to display solar panel output and home power usage on the SHA2017 badge display.

For more info see https://www.projectgus.com/2022/01/sha2017-badge-solar-monitor/

This code isn't a great example of anything and isn't suitable for deployment by anyone, anywhere...

## Uploading app code directly

The SHA2017 badge firmware is a bit different to normal MicroPython. The best tool I've found to interact with it is `ampy`.

Strategy that's worked for me has been:

* Use interactive serial console (i.e. `miniterm.py --raw /dev/ttyUSB0 115200`)
* Reset if needed (Ctrl-C)
* Select "Home" on the serial menu
* Select "Python Shell" on the serial menu
* Confirm >>> REPLY prompt is there
* Exit interactive console Run `ampy -p /dev/ttyUSB0 <command>`

## Other Misc Commands/Notes

```py
import badge

badge.nvs_set_str('system', 'default_app', 'dashboard.home')

badge.nvs_set_str('system', 'default_app', 'solar_usage')

badge.nvs_set_str('solar_usage', 'influxdb_url', ...)
badge.nvs_set_str('solar_usage', 'influxdb_token, ...)
```

To reset to recovery dashboard app, hold Start button when booting. Seems maybe not REPL available in this app?

