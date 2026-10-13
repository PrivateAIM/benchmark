import os
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from benchmarks.measurements import run_metadata, sample_statistics
from benchmarks.runner import _raw_output_dir
from benchmarks.summaries import runner_side_result, write_summary

SIZES_BYTES = [1024, 100_000, 1_000_000, 10_000_000]
PLAINTEXT_CHUNK_BYTES = 1024 * 1024 - 28


def _derive(private_key, public_key) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=None).derive(
        private_key.exchange(ec.ECDH(), public_key)
    )


def encryption_samples(size: int, repeats: int) -> dict:
    assert size > 0 and repeats > 0
    sender = ec.generate_private_key(ec.SECP256R1())
    recipient = ec.generate_private_key(ec.SECP256R1())
    payload = os.urandom(size)
    chunks = [payload[i:i + PLAINTEXT_CHUNK_BYTES] for i in range(0, size, PLAINTEXT_CHUNK_BYTES)]
    copy_seconds, encrypt_seconds, decrypt_seconds = [], [], []
    for index in range(repeats + 1):
        start = time.perf_counter()
        copied = [memoryview(chunk).tobytes() for chunk in chunks]
        copy_elapsed = time.perf_counter() - start
        start = time.perf_counter()
        encrypted = []
        for chunk in chunks:
            key = _derive(sender, recipient.public_key())
            nonce = os.urandom(12)
            encrypted.append(nonce + AESGCM(key).encrypt(nonce, chunk, b""))
        encrypt_elapsed = time.perf_counter() - start
        start = time.perf_counter()
        decrypted = [AESGCM(_derive(recipient, sender.public_key())).decrypt(
            chunk[:12], chunk[12:], b""
        ) for chunk in encrypted]
        decrypt_elapsed = time.perf_counter() - start
        assert copied == chunks == decrypted, "Encryption round-trip corrupted bytes."
        if index:
            copy_seconds.append(copy_elapsed)
            encrypt_seconds.append(encrypt_elapsed)
            decrypt_seconds.append(decrypt_elapsed)
    return {
        "size_bytes": size, "encrypted_bytes": sum(map(len, encrypted)),
        "chunks": len(chunks), "copy_seconds": copy_seconds,
        "encrypt_seconds": encrypt_seconds, "decrypt_seconds": decrypt_seconds,
        "encrypt_minus_copy_seconds": [e - c for e, c in zip(encrypt_seconds, copy_seconds)],
        "statistics": {"encrypt_seconds": sample_statistics(encrypt_seconds),
                       "decrypt_seconds": sample_statistics(decrypt_seconds)},
    }


def run_encryption_benchmark(repeats: int = 3, environment: str = "", condition: str = "unspecified",
                             node_info: dict | None = None, seed: int = 0, analysis_retries: int = 0) -> str:
    started = time.perf_counter()
    payload = {
        "overall_success": True,
        "scope": "Local reference primitive benchmark; not measured node-service or cluster overhead",
        "source": "https://github.com/PrivateAIM/node-storage-service/blob/main/project/crypto.py",
        "cipher": "AES-256-GCM", "key_derivation": "ECDH P-256 + HKDF-SHA256",
        "plaintext_chunk_bytes": PLAINTEXT_CHUNK_BYTES,
        "key_generation_timed": False, "warmup_samples_excluded": 1,
        "measurements": [encryption_samples(size, repeats) for size in SIZES_BYTES],
    }
    result = runner_side_result("encryption", "encryption_benchmark.py", payload, time.perf_counter() - started)
    result["run_metadata"] = run_metadata(environment, condition, {} if node_info is None else node_info)
    return write_summary([result], _raw_output_dir(), prefix="encryption_summary")
