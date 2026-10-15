"""aup3_doc.py - decode the binary project document inside an Audacity 3 .aup3 file (ProjectSerializer format)
into a simple tree, and list wave tracks -> clips -> sample blocks. Pure Python, read-only."""
import struct

FT = ["CharSize", "StartTag", "EndTag", "String", "Int", "Bool", "Long", "LongLong", "SizeT",
      "Float", "Double", "Data", "Raw", "Push", "Pop", "Name"]


def decode(dict_blob, doc_blob):
    names, cs = {}, 2

    def rd_str(b, i, n):
        raw = b[i:i + n]
        return raw.decode("utf-16-le" if cs == 2 else "utf-32-le" if cs == 4 else "utf-8", "replace")

    # dictionary of names
    b, i = dict_blob, 0
    while i < len(b):
        t = b[i]; i += 1
        if t == 0:
            cs = b[i]; i += 1
        elif t == 15:
            nid, ln = struct.unpack_from("<HH", b, i); i += 4
            names[nid] = rd_str(b, i, ln); i += ln
        else:
            raise ValueError(f"dict: unexpected type {t} at {i}")
    # document
    root = {"tag": "#root", "attrs": {}, "children": []}
    stack = [root]
    b, i = doc_blob, 0
    while i < len(b):
        t = b[i]; i += 1
        if t == 0:
            cs = b[i]; i += 1
        elif t == 1:
            nid, = struct.unpack_from("<H", b, i); i += 2
            node = {"tag": names.get(nid, f"?{nid}"), "attrs": {}, "children": []}
            stack[-1]["children"].append(node); stack.append(node)
        elif t == 2:
            i += 2; stack.pop()
        elif t == 3:
            nid, ln = struct.unpack_from("<Hi", b, i); i += 6
            stack[-1]["attrs"][names.get(nid, nid)] = rd_str(b, i, ln); i += ln
        elif t in (4, 6):
            nid, v = struct.unpack_from("<Hi", b, i); i += 6
            stack[-1]["attrs"][names.get(nid, nid)] = v
        elif t == 5:
            nid, v = struct.unpack_from("<HB", b, i); i += 3
            stack[-1]["attrs"][names.get(nid, nid)] = bool(v)
        elif t == 7:
            nid, v = struct.unpack_from("<Hq", b, i); i += 10
            stack[-1]["attrs"][names.get(nid, nid)] = v
        elif t == 8:
            nid, v = struct.unpack_from("<HI", b, i); i += 6
            stack[-1]["attrs"][names.get(nid, nid)] = v
        elif t == 9:
            nid, v, d = struct.unpack_from("<Hfi", b, i); i += 10
            stack[-1]["attrs"][names.get(nid, nid)] = v
        elif t == 10:
            nid, v, d = struct.unpack_from("<Hdi", b, i); i += 14
            stack[-1]["attrs"][names.get(nid, nid)] = v
        elif t in (11, 12):
            ln, = struct.unpack_from("<i", b, i); i += 4 + ln
        elif t in (13, 14):
            pass
        elif t == 15:
            nid, ln = struct.unpack_from("<HH", b, i); i += 4
            names[nid] = rd_str(b, i, ln); i += ln
        else:
            raise ValueError(f"doc: unknown field type {t} at offset {i - 1}")
    return root


def walk(node, tag):
    if node["tag"] == tag:
        yield node
    for c in node["children"]:
        yield from walk(c, tag)


def tracks(root):
    """[{name, rate, gain, pan, mute, solo, channel, clips:[{offset, trim_left, blocks:[(start, blockid)], numsamples}]}]"""
    out = []
    for wt in walk(root, "wavetrack"):
        a = wt["attrs"]
        tr = {"name": a.get("name"), "rate": a.get("rate"), "gain": a.get("gain", 1.0), "pan": a.get("pan", 0.0),
              "mute": a.get("mute", False), "solo": a.get("solo", False), "channel": a.get("channel"),
              "linked": a.get("linked"), "clips": []}
        for wc in walk(wt, "waveclip"):
            ca = wc["attrs"]
            for sq in walk(wc, "sequence"):
                blocks = [(bl["attrs"].get("start"), bl["attrs"].get("blockid")) for bl in walk(sq, "waveblock")]
                tr["clips"].append({"offset": ca.get("offset", 0.0), "trimLeft": ca.get("trimLeft", 0.0),
                                    "trimRight": ca.get("trimRight", 0.0), "rate": ca.get("rate"),
                                    "numsamples": sq["attrs"].get("numsamples"), "blocks": blocks})
        out.append(tr)
    return out


if __name__ == "__main__":
    import sys, json
    d = open(sys.argv[1], "rb").read(); doc = open(sys.argv[2], "rb").read()
    root = decode(d, doc)
    for t in tracks(root):
        print({k: v for k, v in t.items() if k != "clips"})
        for c in t["clips"]:
            print("   clip", {k: v for k, v in c.items() if k != "blocks"}, "blocks:", len(c["blocks"]),
                  c["blocks"][:2])
