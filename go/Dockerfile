# go/Dockerfile
FROM golang:1.22-alpine AS builder

RUN apk add --no-cache git

WORKDIR /build
COPY go.mod go.sum* ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -ldflags="-w -s" -o biopay-gateway ./cmd/server

# ── Minimal runtime image ──────────────────────────────────────────────────
FROM alpine:3.19

RUN apk add --no-cache ca-certificates curl

RUN adduser -D -u 1001 biopay
WORKDIR /app
COPY --from=builder /build/biopay-gateway .

USER biopay
EXPOSE 8080

CMD ["./biopay-gateway"]
