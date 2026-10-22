from pathlib import Path
from typing import Iterator

from .config import settings


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def stream(self, key: str, chunk: int = 1 << 20) -> Iterator[bytes]:
        with self._path(key).open("rb") as f:
            while data := f.read(chunk):
                yield data

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Storage:
    def __init__(self, bucket: str, prefix: str, kms_key_id: str = ""):
        import boto3

        self.s3 = boto3.client("s3")
        self.bucket, self.prefix, self.kms = bucket, prefix, kms_key_id

    def _extra(self) -> dict:
        if self.kms:
            return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.kms}
        return {"ServerSideEncryption": "AES256"}

    def put(self, key: str, data: bytes) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=data,
                           ContentType="application/pdf", **self._extra())

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=self.prefix + key)["Body"].read()

    def stream(self, key: str, chunk: int = 1 << 20) -> Iterator[bytes]:
        body = self.s3.get_object(Bucket=self.bucket, Key=self.prefix + key)["Body"]
        yield from body.iter_chunks(chunk)

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=self.prefix + key)


storage = (
    S3Storage(settings.s3_bucket, settings.s3_prefix, settings.s3_kms_key_id)
    if settings.storage_backend == "s3"
    else LocalStorage(settings.data_dir / "files")
)
