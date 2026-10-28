import hashlib, sys
sys.path.insert(0, ".")
from rpc import b58d, b58e
P = 2**255 - 19; D = (-121665 * pow(121666, P-2, P)) % P
def on_curve(b):
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    if y >= P: return False
    u = (y*y - 1) % P; v = (D*y*y + 1) % P
    x2 = u * pow(v, P-2, P) % P
    return x2 == 0 or pow(x2, (P-1)//2, P) == 1
def find_pda(seeds, program):
    pid = b58d(program)
    for bump in range(255, -1, -1):
        h = hashlib.sha256(b"".join(seeds) + bytes([bump]) + pid + b"ProgramDerivedAddress").digest()
        if not on_curve(h): return b58e(h), bump
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
if __name__ == "__main__":
    print(find_pda([b"global"], PUMP))   # expect 4wTV1Ymi...
    print(find_pda([b"holder-rewards", b58d("51SfaAyrYaR8FpibXT4TXtxEpnZSNoDwYVRojtKupump")], PUMP))  # expect AmzruX2...
