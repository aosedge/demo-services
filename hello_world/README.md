# AOS Edge Service - C++ Hello World Example

Service documentation reference: [Develop your service](https://stage.docs.aosedge.tech/docs/next/how-to/tutorials/service-managment/develop-your-service/hello-world/)

Build binary from shared source:

- `./build.sh --toolchain=/path/to/environment-setup-core2-64-aos-linux`
- Optional: `--arch=<name>` (default is `x86`)
- Output: `service/<arch>/hello_world`

The script requires `--toolchain`, sources it, and then runs CMake + Make.
