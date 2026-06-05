# client-server

Service documentation reference: [Develop your service](https://stage.docs.aosedge.tech/docs/next/how-to/tutorials/service-managment/develop-your-service/client-server/)

Build binaries from source:

- `./build.sh --toolchain=/path/to/environment-setup-core2-64-aos-linux`
- Optional: `--arch=<name>` (default is `x86`)

Build output:

- `service/server/<arch>/aos_http_server`
- `service/client/<arch>/aos_http_client`

The script requires `--toolchain`, sources it, and then runs CMake + Make.
