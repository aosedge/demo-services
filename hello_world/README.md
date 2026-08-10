# AOS Edge Service - C++ Hello World Example

Service documentation reference: [Develop your service](https://stage.docs.aosedge.tech/docs/next/how-to/tutorials/service-managment/develop-your-service/hello-world/)

Build binary from shared source, using the shared `build.sh` at the repo root:

```console
../build.sh . --toolchain=/path/to/environment-setup-core2-64-aos-linux
```

- `--toolchain` is optional; if omitted, the current environment's toolchain is used;
- `--arch=<name>` is optional (defaults to `amd64`, matching `config.yaml`'s `sourceFolder: amd64`).
