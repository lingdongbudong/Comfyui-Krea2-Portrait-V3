# -*- coding: utf-8 -*-
"""
Comfyui-Krea2-Portrait-V3 :: PNG 元数据查看 / 探针工具（v3.21 新增）

纯标准库实现（不依赖 PIL / numpy），直接解析与改写 PNG 的 tEXt / iTXt 块。


【两个用途】

1) 看一眼某张图到底有没有把提示词写进元数据 —— 排障用：

       python tools/inspect_png_metadata.py "路径/图.png"

   会打印所有元数据键、并从 workflow 里读出"看图工具会看到的那段正向提示词"。

2) 探针：给一张**已有**的图注入一段标记文本，另存成一张新图，
   用来验证"你的看图工具到底读不读得到" —— 不用重新出图：

       python tools/inspect_png_metadata.py "路径/图.png" --probe "探针图.png"

   ⚠️ 探针只改 PNG 的元数据副本，**不动原图**，也不参与任何出图逻辑。
      如果看图工具在探针图上显示了这段标记文本，说明这条修复路径对它是有效的。
"""

import json
import os
import struct
import sys
import zlib

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)

# 直接按文件路径加载注入模块（它只依赖标准库，不需要装成包）
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("k2_v3_pngmeta",
                                     os.path.join(_PKG, "k2_v3_pngmeta.py"))
PM = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(PM)

_SIG = b"\x89PNG\r\n\x1a\n"
# 我们只处理这两种文本块；其余块原样保留
_TEXT_CHUNKS = (b"tEXt", b"iTXt", b"zTXt")


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------

def read_png_chunks(path):
    """返回 [(chunk_type, data, crc_bytes), …]（不含签名）。"""
    out = []
    with open(path, "rb") as f:
        if f.read(8) != _SIG:
            raise ValueError("不是 PNG 文件：%s" % path)
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            ln, typ = struct.unpack(">I4s", hdr)
            data = f.read(ln)
            crc = f.read(4)
            out.append((typ, data, crc))
            if typ == b"IEND":
                break
    return out


def decode_text(typ, data):
    """把文本块解成 (键, 值)；解不出来返回 (None, None)。"""
    try:
        if typ == b"tEXt":
            k, v = data.split(b"\x00", 1)
            return k.decode("latin-1"), v.decode("utf-8", "replace")
        if typ == b"iTXt":
            # 关键字\0压缩标志\0压缩方法\0语言\0译名\0文本
            parts = data.split(b"\x00", 5)
            if len(parts) < 6:
                return None, None
            k, comp, _m, _lang, _name, text = parts
            if comp == b"\x01":
                text = zlib.decompress(text)
            return k.decode("latin-1"), text.decode("utf-8", "replace")
        if typ == b"zTXt":
            k, rest = data.split(b"\x00", 1)
            return k.decode("latin-1"), zlib.decompress(rest[1:]).decode("utf-8", "replace")
    except Exception:
        pass
    return None, None


def read_png_text(path):
    """返回 {键: 值}（同键取**第一个** —— 这也是多数看图工具的行为）。"""
    out = {}
    for typ, data, _crc in read_png_chunks(path):
        if typ in _TEXT_CHUNKS:
            k, v = decode_text(typ, data)
            if k and k not in out:
                out[k] = v
    return out


# ---------------------------------------------------------------------------
# 写（探针用）
# ---------------------------------------------------------------------------

def _make_text_chunk(key, value):
    data = key.encode("latin-1") + b"\x00" + str(value).encode("utf-8")
    crc = zlib.crc32(b"tEXt" + data) & 0xFFFFFFFF
    return (b"tEXt", data, struct.pack(">I", crc))


def write_png_with_text(src, dst, updates):
    """把 `updates`={键: 值} 写进 dst（先删同名旧块，插在 IHDR 之后）。"""
    chunks = read_png_chunks(src)
    keys = set(updates)
    out = [(_SIG, None, None)]
    inserted = False
    for typ, data, crc in chunks:
        if typ in _TEXT_CHUNKS:
            k, _v = decode_text(typ, data)
            if k in keys:
                continue                      # 丢掉旧的同名块
        out.append((typ, data, crc))
        if typ == b"IHDR" and not inserted:
            for k, v in updates.items():
                out.append(_make_text_chunk(k, v))
            inserted = True
    with open(dst, "wb") as f:
        for typ, data, crc in out:
            if typ == _SIG:
                f.write(_SIG)
                continue
            f.write(struct.pack(">I", len(data)) + typ + data + crc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def report(path):
    print("=" * 74)
    print("文件：%s" % path)
    print("大小：%.2f MB" % (os.path.getsize(path) / 1048576.0))
    print("=" * 74)
    text = read_png_text(path)
    if not text:
        print("⚠ PNG 里**没有任何文本元数据块**（tEXt/iTXt/zTXt 都没有）。")
        return
    print("元数据键（共 %d 个）：" % len(text))
    for k, v in text.items():
        print("   %-22s %8d 字节   %s" % (
            k, len(v), (v[:60].replace("\n", " ") + "…") if len(v) > 60 else v))

    # 从 workflow / prompt 里找"看图工具会看到的那段正向提示词"
    print()
    print("-" * 74)
    print("正向提示词（按看图工具的常见读法提取）：")
    found = ""
    why = ""
    if "workflow" in text:
        try:
            wf = json.loads(text["workflow"])
            got = PM.dump_workflow_prompt(wf)
            if got:
                found, why = got, "workflow（UI 格式）里的正向编码节点"
        except Exception as e:
            why = "workflow 解析失败：%s" % e
    if not found and "prompt" in text:
        try:
            api = json.loads(text["prompt"])
            for nid, nd in (api or {}).items():
                if isinstance(nd, dict) and nd.get("class_type") == "CLIPTextEncode":
                    for key in ("text", PM.INJECT_KEY):
                        v = (nd.get("inputs") or {}).get(key)
                        if isinstance(v, str) and v:
                            found, why = v, "prompt（API 格式）%s#%s 的 %s" % (
                                "CLIPTextEncode", nid, key)
                            break
                if found:
                    break
        except Exception as e:
            why = why or ("prompt 解析失败：%s" % e)
    if not found:
        for key in ("k2v3_prompt", "parameters"):
            if text.get(key):
                found, why = text[key].split("\n")[0], "独立键 %s" % key
                break
    if found:
        print("  来源：%s" % why)
        print("  长度：%d 字" % len(found))
        print("  内容：%s" % found[:400])
    else:
        print("  ❌ 没找到 —— 这张图的元数据里不含完整正向提示词。")
        if why:
            print("     （%s）" % why)


def main():
    args = [a for a in sys.argv[1:]]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    path = args[0]
    if not os.path.isfile(path):
        print("找不到文件：%s" % path)
        return 2

    if "--probe" in args:
        i = args.index("--probe")
        dst = args[i + 1] if i + 1 < len(args) else "探针-元数据测试.png"
        text = read_png_text(path)
        if "workflow" not in text:
            print("⚠ 这张图没有 workflow 元数据，无法做探针。")
            return 1
        wf = json.loads(text["workflow"])
        marker = ("【元数据探针】如果你在看图工具里看到这句话，"
                  "说明工具读的是 workflow 里的正向提示词位置 —— "
                  "插件 v3.21 的修复对它是有效的。")
        st = PM.inject_workflow_text(wf, marker)
        if not st.get("ok"):
            print("⚠ 注入失败：%s" % st.get("reason"))
            return 1
        write_png_with_text(path, dst, {"workflow": json.dumps(wf, ensure_ascii=False)})
        print("✅ 探针已写出：%s" % dst)
        print("   改动位置 %d 处：%s" % (len(st["written"]), "、".join(st["written"])))
        print("   原图未被修改。")
        print()
        print("→ 请用你的看图工具打开这张探针图，看「提示词」是不是显示成那句标记文本。")
        return 0

    report(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
