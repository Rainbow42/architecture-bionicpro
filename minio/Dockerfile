FROM docker.io/library/golang:1.25-bookworm AS build
WORKDIR /src
RUN git clone --depth 1 --branch RELEASE.2025-10-15T17-29-55Z https://github.com/minio/minio.git . \
    && CGO_ENABLED=0 go build -trimpath -o /out/minio .

FROM docker.io/library/debian:bookworm-slim
COPY --from=build /out/minio /usr/local/bin/minio
COPY --from=build /src/LICENSE /usr/share/doc/minio/LICENSE
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
RUN mkdir /data && chown 10001:10001 /data
USER 10001
ENTRYPOINT ["minio"]
CMD ["server", "/data"]
