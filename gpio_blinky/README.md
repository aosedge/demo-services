# GPIO blinky

A demo service that toggles a GPIO pin periodically to create a blinking effect.
The pin is configured as an **output** and controlled using the [`gpiod`](https://pypi.org/project/gpiod/) python library.

## Requirements

- Access to the GPIO device: **`/dev/gpiochip0`**
- Python dependencies provided by the **`aos-rpilibs-layer`**

## Preparing service

### Update unit config to expose the GPIO device

Ensure `/dev/gpiochip0` is available to your service by defining it in the **unit configuration** of the target node:

```json
      "nodeType": "aos-rpi-1.0-single-domd",
      "devices": [
        {
          "name": "gpio",
          "groups": [
            "gpio"
          ],
          "hostDevices": [
            "/dev/gpiochip0"
          ]
        }
      ]
```

See [unit config documentation](https://docs.aosedge.tech/docs/reference/core-component-configs/unit-config) for details.

### Request the GPIO device in service config.yaml

This device should be requested by your service in `config.yaml`:

```yaml
    devices:
        - name: gpio
          mode: rwm
```

### Provide service with all required libraries

This demo service requires specific python libs that are provided by the `aos-rpilibs-layer`.
Add the following entries in you `config.yaml` and upload the dedicated layer to the cloud:

```yaml
    layers:
        - "aos-rpilibs-layer"
```

See [Use layers](https://docs.aosedge.tech/docs/how-to/use-layer) for details.
