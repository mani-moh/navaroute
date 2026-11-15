"""Pure-stdlib AES (128/192/256) block cipher + AES-GCM (NIST SP 800-38D).

Written stdlib-only on purpose: the repo's CI installs no third-party
packages, and the Blackout Kit must work on a bare Python 3.12 install
(and in the standalone decrypt helper, which is a single file). The
implementation is validated against the `cryptography` package and
NIST GCM test vectors in tests/test_blackout_kit.py.

Only the 12-byte-IV path is used by the kit (crypto.py), but the
general IV-length J0 derivation is implemented for completeness.
"""

from __future__ import annotations

# ---------------------------------------------------------------- GF(2^8)

_POLY = 0x11B


def _gf_mul(a: int, b: int) -> int:
    """Multiply in GF(2^8) modulo x^8 + x^4 + x^3 + x + 1."""
    result = 0
    while b:
        if b & 1:
            result ^= a
        a <<= 1
        if a & 0x100:
            a ^= _POLY
        b >>= 1
    return result & 0xFF


def _gf_inv(a: int) -> int:
    if a == 0:
        return 0
    for candidate in range(1, 256):
        if _gf_mul(a, candidate) == 1:
            return candidate
    raise ValueError("no inverse in GF(2^8)")


def _rotl8(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (8 - shift))) & 0xFF


def _build_sboxes() -> tuple[list[int], list[int]]:
    sbox = [0] * 256
    for value in range(256):
        inv = _gf_inv(value)
        sbox[value] = (
            inv ^ _rotl8(inv, 1) ^ _rotl8(inv, 2) ^ _rotl8(inv, 3) ^ _rotl8(inv, 4) ^ 0x63
        )
    inv_sbox = [0] * 256
    for value, substituted in enumerate(sbox):
        inv_sbox[substituted] = value
    return sbox, inv_sbox


SBOX, INV_SBOX = _build_sboxes()

# Round constants for key expansion (Rcon[i] = 2^(i-1) in GF(2^8)).
RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


# ---------------------------------------------------------------- AES block


def _expand_key(key: bytes) -> list[list[int]]:
    """Return the round keys as a list of 16-byte states (column-major)."""
    if len(key) not in (16, 24, 32):
        raise ValueError("AES key must be 16, 24 or 32 bytes")
    nk = len(key) // 4
    nr = nk + 6
    words: list[list[int]] = [list(key[4 * i : 4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        temp = list(words[i - 1])
        if i % nk == 0:
            temp = [SBOX[temp[1]] ^ RCON[i // nk - 1], SBOX[temp[2]], SBOX[temp[3]], SBOX[temp[0]]]
        elif nk > 6 and i % nk == 4:
            temp = [SBOX[b] for b in temp]
        words.append([words[i - nk][j] ^ temp[j] for j in range(4)])
    return [sum((words[4 * r + c] for c in range(4)), []) for r in range(nr + 1)]


def _add_round_key(state: list[int], round_key: list[int]) -> None:
    for i in range(16):
        state[i] ^= round_key[i]


def _sub_bytes(state: list[int]) -> None:
    for i in range(16):
        state[i] = SBOX[state[i]]


def _shift_rows(state: list[int]) -> None:
    # State is column-major: state[4*c + r] is row r, column c.
    old = list(state)
    for r in range(4):
        for c in range(4):
            state[4 * c + r] = old[4 * ((c + r) % 4) + r]


def _mix_columns(state: list[int]) -> None:
    for c in range(4):
        col = state[4 * c : 4 * c + 4]
        a0, a1, a2, a3 = col
        state[4 * c + 0] = _gf_mul(a0, 2) ^ _gf_mul(a1, 3) ^ a2 ^ a3
        state[4 * c + 1] = a0 ^ _gf_mul(a1, 2) ^ _gf_mul(a2, 3) ^ a3
        state[4 * c + 2] = a0 ^ a1 ^ _gf_mul(a2, 2) ^ _gf_mul(a3, 3)
        state[4 * c + 3] = _gf_mul(a0, 3) ^ a1 ^ a2 ^ _gf_mul(a3, 2)


def aes_encrypt_block(key: bytes, block: bytes) -> bytes:
    """Encrypt one 16-byte block under `key` (16/24/32 bytes)."""
    if len(block) != 16:
        raise ValueError("AES block must be 16 bytes")
    round_keys = _expand_key(key)
    state = list(block)
    _add_round_key(state, round_keys[0])
    for rnd in range(1, len(round_keys) - 1):
        _sub_bytes(state)
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, round_keys[rnd])
    _sub_bytes(state)
    _shift_rows(state)
    _add_round_key(state, round_keys[-1])
    return bytes(state)


# ---------------------------------------------------------------- GF(2^128)


def _gf128_mul(x: int, y: int) -> int:
    """Multiply in GF(2^128) with the GCM polynomial (NIST SP 800-38D)."""
    z = 0
    v = y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ 0xE1000000000000000000000000000000
        else:
            v >>= 1
    return z


def _ghash(h: int, data: bytes) -> int:
    """GHASH over `data` (zero-padded to a multiple of 16 bytes)."""
    y = 0
    for offset in range(0, len(data), 16):
        block = data[offset : offset + 16].ljust(16, b"\x00")
        y = _gf128_mul(y ^ int.from_bytes(block, "big"), h)
    return y


def _inc32(counter: bytes) -> bytes:
    value = int.from_bytes(counter, "big")
    low = (value & 0xFFFFFFFF) + 1
    return (value & ~0xFFFFFFFF | (low & 0xFFFFFFFF)).to_bytes(16, "big")


# ---------------------------------------------------------------- GCM


def _j0(key: bytes, iv: bytes) -> bytes:
    if len(iv) == 12:
        return iv + b"\x00\x00\x00\x01"
    h = int.from_bytes(aes_encrypt_block(key, b"\x00" * 16), "big")
    # J0 = GHASH_H(IV || 0^(s+64) || [len(IV)]_64), s pads IV to 128 bits.
    pad_len = (16 - len(iv) % 16) % 16 + 8
    padded = iv + b"\x00" * pad_len + (len(iv) * 8).to_bytes(8, "big")
    return _ghash(h, padded).to_bytes(16, "big")


def _gctr(key: bytes, icb: bytes, data: bytes) -> bytes:
    out = bytearray()
    counter = icb
    for offset in range(0, len(data), 16):
        keystream = aes_encrypt_block(key, counter)
        chunk = data[offset : offset + 16]
        out.extend(b ^ k for b, k in zip(chunk, keystream))
        counter = _inc32(counter)
    return bytes(out)


def _tag(key: bytes, j0: bytes, h: int, ciphertext: bytes, aad: bytes = b"") -> bytes:
    """GCM authentication tag for `ciphertext` (GHASH over AAD + ciphertext)."""
    lengths = (len(aad) * 8).to_bytes(8, "big") + (len(ciphertext) * 8).to_bytes(8, "big")
    s = _ghash(h, aad + b"\x00" * ((16 - len(aad) % 16) % 16)
               + ciphertext + b"\x00" * ((16 - len(ciphertext) % 16) % 16) + lengths)
    return (s ^ int.from_bytes(aes_encrypt_block(key, j0), "big")).to_bytes(16, "big")


def aes_gcm_encrypt(key: bytes, iv: bytes, plaintext: bytes, aad: bytes = b"") -> tuple[bytes, bytes]:
    """Return (ciphertext, tag) for AES-GCM. `iv` is 12 bytes in the kit."""
    if len(key) not in (16, 24, 32):
        raise ValueError("AES key must be 16, 24 or 32 bytes")
    if not iv:
        raise ValueError("IV must not be empty")
    h = int.from_bytes(aes_encrypt_block(key, b"\x00" * 16), "big")
    j0 = _j0(key, iv)
    ciphertext = _gctr(key, _inc32(j0), plaintext)
    return ciphertext, _tag(key, j0, h, ciphertext, aad)


def aes_gcm_decrypt(key: bytes, iv: bytes, ciphertext: bytes, tag: bytes, aad: bytes = b"") -> bytes:
    """Decrypt AES-GCM; raises ValueError when the tag does not verify."""
    if len(key) not in (16, 24, 32):
        raise ValueError("AES key must be 16, 24 or 32 bytes")
    if not iv:
        raise ValueError("IV must not be empty")
    h = int.from_bytes(aes_encrypt_block(key, b"\x00" * 16), "big")
    j0 = _j0(key, iv)
    if _tag(key, j0, h, ciphertext, aad) != tag:
        raise ValueError("AES-GCM authentication failed (bad tag)")
    return _gctr(key, _inc32(j0), ciphertext)
