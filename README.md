# Mijia Temperature Bluetooth
Home-assistant sensor platform for Xiaomi Mijia BT temperature and humidity sensor

## Usage
```
sensor:
 - platform: mitemp_bt
    name: Kitchen
    mac: 'xx:xx:xx:xx:xx:xx'
    force_update: false
    median: 1
    monitored_conditions:
      - temperature
      - humidity
      - battery
```

mac is required.

default "scan" isterval is every 1200 seconds.
