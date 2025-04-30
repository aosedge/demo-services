# KUKSA test client

This is demo service that implements KUKSA client and periodically retrieves `Vehicle.Speed` value from KUKSA data
broker. KUKSA data broker is installed on the main node and binds to all interfaces. The main node has static IP:
10.0.0.100. This demo service includes KUKSA root certificate and access token required to connect to and authorize with
KUKSA data broker.

## Preparing service

KUKSA client should know where KUKSA data broker is located. For this purpose, a special resource should be added to your
unit configuration for main node. Check
[example unit config](https://github.com/aosedge/meta-aos-vm/blob/main/misc/unitconfig.json):

```json
    "nodeType": "aos-vm-1.0-main-genericx86-64",
    ...
    "resources": [
        {
            "name": "kuksa",
            "hosts": [
                {
                    "ip": "10.0.0.100",
                    "hostname": "Server"
                }
            ]
        }
    ]
```

See [Unit config](https://docs.aosedge.tech/docs/reference/core-component-configs/unit-config) for details.

This resource should be requested by your service in `config.yaml`:

```yaml
    resources:
        - kuksa
```

Optionally, if additional python libraries are required, they can be added by attaching `aos-pylibs-layer` to your
service. Add the following entries in you `config.yaml` and upload the dedicated layer to the cloud:

```yaml
    layers:
        - "aos-pylibs-layer"
```

See [Use layers](https://docs.aosedge.tech/docs/how-to/use-layer) for details.
