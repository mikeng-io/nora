import base64, json, ssl, struct, urllib.request, subprocess, os, re

PROFILE = "gezae3hoez"   # from the ctrld unit's --cd flag

def dns_query(name, qtype=1):
    tid = 0x1234
    h = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    q = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    return h + q + struct.pack(">HH", qtype, 1)

def doh(name, resolver):
    q = dns_query(name)
    b64 = base64.urlsafe_b64encode(q).decode().rstrip("=")
    url = f"https://{resolver}/{PROFILE}?dns={b64}"
    req = urllib.request.Request(url, headers={"Accept":"application/dns-message"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    except Exception as e:
        return None

def parse(resp):
    if not resp: return "no response"
    ancount = struct.unpack(">H", resp[6:8])[0]
    if ancount == 0: return "NOANSWER (NXDOMAIN/empty)"
    off = 12
    while resp[off] != 0:
        off += resp[off] + 1
    off += 5
    out = []
    for _ in range(ancount):
        if resp[off] & 0xC0 == 0xC0: off += 2
        else:
            while resp[off] != 0: off += resp[off]+1
            off += 1
        rtype, _, _, rdlen = struct.unpack(">HHIH", resp[off:off+10]); off += 10
        data = resp[off:off+rdlen]; off += rdlen
        if rtype == 1:  out.append("A " + ".".join(map(str,data)))
        elif rtype == 28: out.append("AAAA " + ":".join(f"{data[i]:02x}{data[i+1]:02x}" for i in range(0,16,2)))
        elif rtype == 5:
            out.append("CNAME(binary)"); 
        else: out.append(f"type{rtype}")
    return ", ".join(out)

print("=== Control D CLOUD verdict (querying their anycast resolver directly, his profile) ===")
for name in ["api.minimaxi.com", "platform.minimaxi.com", "api.minimax.chat", "www.baidu.com"]:
    r = doh(name, "76.76.2.22")
    print(f"  {name:24s} -> {parse(r)}")
print("\n  legend: 0.0.0.0 / :: = blocked BY CONTROL D CLOUD (profile-level rule)")
print("          a real IP = allowed upstream -> the block is NOT cloud-side")
