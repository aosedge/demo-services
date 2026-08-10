# client-server

Service documentation reference: [Develop your service](https://stage.docs.aosedge.tech/docs/next/how-to/tutorials/service-managment/develop-your-service/client-server/)

Build binaries from source, using the shared `build.sh` at the repo root - once for the server, once for the client:

```console
../build.sh server --toolchain=/path/to/environment-setup-core2-64-aos-linux
../build.sh client --toolchain=/path/to/environment-setup-core2-64-aos-linux
```

- `--toolchain` is optional; if omitted, the current environment's toolchain is used;
- `--arch=<name>` is optional (defaults to `amd64`, matching `config.yaml`'s `sourceFolder: amd64`).
