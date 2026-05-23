#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BidKing 对局信息自动化抓取工具

功能：
1. 监听网络流获取房间号（明文房间号+其余密文）
2. 扫描游戏进程内存，通过房间号/事件名定位日志存储位置
3. 提取对局日志JSON（支持UTF-16LE和UTF-8两种编码）
4. 美化JSON输出，保存为可读的.txt文件

支持的事件类型：
- S2C_33_game_start_notify  游戏开始
- S2C_37_game_next_round_notify  下一回合
- S2C_39_game_use_item_notify  使用道具
- S2C_45_game_over_notify  游戏结束
"""

import re
import time
import threading
import os
import sys
import argparse
import json
import hashlib
from datetime import datetime
from typing import List, Set, Dict, Tuple, Optional

import pymem
import pymem.pattern
from scapy.all import sniff, IP, TCP, Raw

PROCESS_NAME = "BidKing.exe"
SERVER_IP = "8.133.195.27"
SERVER_PORT = 10000
ROOM_ID_PATTERN = re.compile(rb'\b(\d{2,5}:\d{12,20})\b')

DEFAULT_SCAN_DURATION = 5 * 60
DEFAULT_SCAN_INTERVAL = 3
DEFAULT_SEARCH_RADIUS = 12288
DEFAULT_OUTPUT_DIR = "logs"

EVENT_TYPES = {
    "game_start": {
        "patterns": ["S2C_33_game_start_notify"],
        "label": "游戏开始",
    },
    "game_next_round": {
        "patterns": ["S2C_37_game_next_round_notify"],
        "label": "下一回合",
    },
    "game_use_item": {
        "patterns": ["S2C_39_game_use_item_notify"],
        "label": "使用道具",
    },
    "game_over": {
        "patterns": ["S2C_45_game_over_notify"],
        "label": "游戏结束",
    },
}

current_room_id = None
scan_stop_event = threading.Event()
scan_counter = 0
saved_json_hashes: Set[str] = set()
saved_lock = threading.Lock()


def _alignment_of(addr: int, base_addr: int) -> int:
    return (addr - base_addr) % 2


def _find_utf16le_brace_candidates(
    data: bytes, alignment: int, brace_char: int
) -> List[int]:
    results = []
    for i in range(alignment, len(data) - 1, 2):
        if data[i] == brace_char and data[i + 1] == 0x00:
            results.append(i)
    return results


def _try_extract_utf16le_json_from_pos(
    data: bytes, start_pos: int, base_addr: int
) -> Optional[Tuple[str, int, int]]:
    balance = 0
    in_string = False
    escape_next = False
    end_pos = -1

    for i in range(start_pos, len(data) - 1, 2):
        lo = data[i]
        hi = data[i + 1]

        if hi != 0x00:
            if in_string and escape_next:
                escape_next = False
            continue

        if escape_next:
            escape_next = False
            continue

        if lo == 0x5C:
            if in_string:
                escape_next = True
            continue

        if lo == 0x22:
            in_string = not in_string
            continue

        if not in_string:
            if lo == 0x7B:
                balance += 1
            elif lo == 0x7D:
                balance -= 1
                if balance == 0:
                    end_pos = i
                    break

    if end_pos == -1:
        return None

    json_bytes = data[start_pos : end_pos + 2]
    try:
        json_str = json_bytes.decode("utf-16le")
    except UnicodeDecodeError:
        return None

    try:
        json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None

    return (json_str, base_addr + start_pos, base_addr + end_pos + 2)


def extract_utf16le_json(
    pm: pymem.Pymem,
    anchor_addr: int,
    search_radius: int = DEFAULT_SEARCH_RADIUS,
    expected_content: Optional[str] = None,
) -> Optional[Tuple[str, int, int]]:
    """
    以 anchor_addr 为中心，在前后 search_radius 字节范围内搜索
    UTF-16LE 编码的 JSON 对象。

    改进点（对比原版）：
    1. 按2字节步长搜索，尊重UTF-16LE对齐
    2. 处理JSON字符串字面量内的花括号（不误计数）
    3. 处理转义字符
    4. 尝试多个 { 候选位置，按距离排序
    5. 验证提取内容为有效JSON
    6. 可选：优先返回包含 expected_content 的JSON

    返回 (json_string, start_addr, end_addr) 或 None。
    """
    start = max(0, anchor_addr - search_radius)
    read_size = search_radius * 2
    try:
        data = pm.read_bytes(start, read_size)
    except Exception:
        return None

    anchor_offset = anchor_addr - start
    alignment = anchor_offset % 2

    open_braces = _find_utf16le_brace_candidates(data, alignment, 0x7B)
    if not open_braces:
        return None

    open_braces.sort(key=lambda x: abs(x - anchor_offset))

    for pos in open_braces:
        result = _try_extract_utf16le_json_from_pos(data, pos, start)
        if result is None:
            continue

        json_str, s_addr, e_addr = result

        if expected_content and expected_content not in json_str:
            continue

        return result

    if expected_content:
        for pos in open_braces:
            result = _try_extract_utf16le_json_from_pos(data, pos, start)
            if result is not None:
                return result

    return None


def extract_utf8_json(
    pm: pymem.Pymem,
    anchor_addr: int,
    search_radius: int = DEFAULT_SEARCH_RADIUS,
    expected_content: Optional[str] = None,
) -> Optional[Tuple[str, int, int]]:
    """
    UTF-8 回退：当 UTF-16LE 提取失败时，尝试在锚点附近搜索
    UTF-8 编码的 JSON 对象。
    """
    start = max(0, anchor_addr - search_radius)
    read_size = search_radius * 2
    try:
        data = pm.read_bytes(start, read_size)
    except Exception:
        return None

    open_braces = []
    for i in range(len(data)):
        if data[i] == 0x7B:
            open_braces.append(i)

    if not open_braces:
        return None

    anchor_offset = anchor_addr - start
    open_braces.sort(key=lambda x: abs(x - anchor_offset))

    for pos in open_braces:
        balance = 0
        in_string = False
        escape_next = False
        end_pos = -1

        for i in range(pos, len(data)):
            b = data[i]
            if escape_next:
                escape_next = False
                continue
            if b == 0x5C and in_string:
                escape_next = True
                continue
            if b == 0x22:
                in_string = not in_string
                continue
            if not in_string:
                if b == 0x7B:
                    balance += 1
                elif b == 0x7D:
                    balance -= 1
                    if balance == 0:
                        end_pos = i
                        break

        if end_pos == -1:
            continue

        json_bytes = data[pos : end_pos + 1]
        try:
            json_str = json_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue

        try:
            json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            continue

        if expected_content and expected_content not in json_str:
            continue

        return (json_str, start + pos, start + end_pos + 1)

    return None


def extract_json_near_event(
    pm: pymem.Pymem,
    event_addr: int,
    event_name: str,
    search_radius: int = DEFAULT_SEARCH_RADIUS,
) -> Optional[Tuple[str, int, int]]:
    """
    事件字符串专用的 JSON 提取策略。

    策略：
    1. 先尝试 UTF-16LE 提取，优先返回包含事件名的JSON
    2. 再尝试 UTF-16LE 提取，不要求包含事件名（放宽条件）
    3. 最后尝试 UTF-8 回退提取

    事件字符串在内存中可能是：
    - JSON 内部的值：{"cmd":"S2C_33_game_start_notify",...}  → { 在事件名之前
    - JSON 前的标签：S2C_33_game_start_notify {...}          → { 在事件名之后
    - 独立存储：事件名和JSON分属不同对象                       → 需要更大搜索半径
    """
    result = extract_utf16le_json(
        pm, event_addr, search_radius, expected_content=event_name
    )
    if result:
        return result

    result = extract_utf16le_json(pm, event_addr, search_radius)
    if result:
        return result

    result = extract_utf8_json(
        pm, event_addr, search_radius, expected_content=event_name
    )
    if result:
        return result

    result = extract_utf8_json(pm, event_addr, search_radius)
    if result:
        return result

    return None


def scan_room_id_suffix(
    pm: pymem.Pymem, suffix: str
) -> List[Tuple[int, bytes, bytes]]:
    if not suffix or not suffix.isdigit():
        return []
    pattern = b"".join(ch.encode("utf-16le") for ch in suffix)
    results = []
    try:
        matches = pymem.pattern.pattern_scan_all(
            pm.process_handle, pattern, return_multiple=True
        )
        for addr in matches:
            start_before = max(0, addr - 512)
            size_before = addr - start_before
            before_bytes = (
                pm.read_bytes(start_before, size_before) if size_before > 0 else b""
            )
            after_bytes = (
                pm.read_bytes(addr, 4096) if addr < 0x7FFFFFFFFFFFFFFF else b""
            )
            results.append((addr, before_bytes, after_bytes))
    except Exception as e:
        print(f"  扫描房间号后缀时出错: {e}")
    return results


def scan_utf16le_string(pm: pymem.Pymem, target_str: str) -> List[int]:
    if not target_str:
        return []
    pattern = b"".join(ch.encode("utf-16le") for ch in target_str)
    matches = []
    try:
        addrs = pymem.pattern.pattern_scan_all(
            pm.process_handle, pattern, return_multiple=True
        )
        matches = list(addrs) if addrs else []
    except Exception as e:
        print(f"  扫描 UTF-16LE 字符串 '{target_str}' 时出错: {e}")
    return matches


def format_hex_dump(data: bytes) -> str:
    if not data:
        return "(空)\n"
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i : i + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {i:04x}: {hex_part:<48} {ascii_part}")
    return "\n".join(lines)


def _json_content_hash(json_str: str) -> str:
    try:
        parsed = json.loads(json_str)
        canonical = json.dumps(parsed, sort_keys=True, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        canonical = json_str
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()


def _already_saved(json_str: str) -> bool:
    h = _json_content_hash(json_str)
    with saved_lock:
        if h in saved_json_hashes:
            return True
        saved_json_hashes.add(h)
        return False


def save_json_log(
    room_id: str,
    scan_idx: int,
    json_str: str,
    addr_start: int,
    addr_end: int,
    event_type: Optional[str] = None,
    output_dir: str = DEFAULT_OUTPUT_DIR,
):
    if _already_saved(json_str):
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_room_id = room_id.replace(":", "_")
    event_tag = f"_{event_type}" if event_type else ""
    filename = os.path.join(
        output_dir,
        f"json_log_{safe_room_id}_{scan_idx:03d}{event_tag}_{timestamp}.txt",
    )

    try:
        parsed = json.loads(json_str)
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        pretty = json_str

    event_label = ""
    if event_type and event_type in EVENT_TYPES:
        event_label = f"事件类型: {EVENT_TYPES[event_type]['label']} ({event_type})\n"

    header = (
        f"房间号: {room_id}\n"
        f"扫描次数: {scan_idx}\n"
        f"内存地址范围: 0x{addr_start:X} - 0x{addr_end:X}\n"
        f"时间: {datetime.now().isoformat()}\n"
        f"{event_label}"
        f"{'=' * 80}\n\n"
    )

    os.makedirs(output_dir, exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(pretty)

    print(f"  JSON 日志已保存: {filename}")


def save_debug_hex(
    room_id: str,
    scan_idx: int,
    event_name: str,
    addr: int,
    data: bytes,
    label: str,
    output_dir: str = DEFAULT_OUTPUT_DIR,
):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_room_id = room_id.replace(":", "_")
    filename = os.path.join(
        output_dir,
        f"debug_{safe_room_id}_{scan_idx:03d}_{event_name}_{timestamp}.txt",
    )
    os.makedirs(output_dir, exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"房间号: {room_id}\n")
        f.write(f"事件: {event_name}\n")
        f.write(f"地址: 0x{addr:X}\n")
        f.write(f"标签: {label}\n")
        f.write(f"时间: {datetime.now().isoformat()}\n")
        f.write("=" * 80 + "\n\n")
        f.write(format_hex_dump(data))
    print(f"  调试数据已保存: {filename}")


def scanning_worker(
    pm: pymem.Pymem,
    room_id: str,
    scan_duration: int,
    scan_interval: int,
    search_radius: int,
    output_dir: str,
):
    global scan_counter
    start_time = time.time()
    scan_counter = 0

    room_suffix = ""
    if ":" in room_id:
        room_suffix = room_id.split(":", 1)[1]

    print(f"[扫描] 开始 | 房间号: {room_id} | 后缀: {room_suffix} | 持续: {scan_duration}s | 间隔: {scan_interval}s")

    while not scan_stop_event.is_set():
        elapsed = time.time() - start_time
        if elapsed > scan_duration:
            print("[扫描] 时间已到，停止扫描。")
            break

        scan_counter += 1
        print(f"\n{'='*60}")
        print(f"第 {scan_counter} 次扫描 | 已过 {elapsed:.0f}s / {scan_duration}s")
        print(f"{'='*60}")

        if room_suffix:
            _scan_by_room_suffix(pm, room_id, room_suffix, scan_counter, search_radius, output_dir)

        for event_key, event_info in EVENT_TYPES.items():
            _scan_by_event(pm, room_id, event_key, event_info, scan_counter, search_radius, output_dir)

        scan_stop_event.wait(scan_interval)


def _scan_by_room_suffix(
    pm, room_id, room_suffix, scan_idx, search_radius, output_dir
):
    print(f"  [房间后缀] 搜索 {room_suffix} ...")
    try:
        suffix_matches = scan_room_id_suffix(pm, room_suffix)
        if not suffix_matches:
            print("  [房间后缀] 未找到匹配")
            return

        print(f"  [房间后缀] 找到 {len(suffix_matches)} 个匹配地址")
        for addr, before, after in suffix_matches:
            result = extract_utf16le_json(pm, addr, search_radius)
            if result:
                json_str, s_addr, e_addr = result
                save_json_log(room_id, scan_idx, json_str, s_addr, e_addr, output_dir=output_dir)
            else:
                print(f"  [房间后缀] 0x{addr:X} 附近未找到有效JSON")
    except Exception as e:
        print(f"  [房间后缀] 扫描出错: {e}")


def _scan_by_event(
    pm, room_id, event_key, event_info, scan_idx, search_radius, output_dir
):
    for pattern_str in event_info["patterns"]:
        label = event_info["label"]
        print(f"  [{label}] 搜索 {pattern_str} ...")
        try:
            addrs = scan_utf16le_string(pm, pattern_str)
            if not addrs:
                print(f"  [{label}] 未找到字符串")
                continue

            print(f"  [{label}] 找到 {len(addrs)} 个匹配地址")
            for addr in addrs:
                result = extract_json_near_event(
                    pm, addr, pattern_str, search_radius
                )
                if result:
                    json_str, s_addr, e_addr = result
                    save_json_log(
                        room_id,
                        scan_idx,
                        json_str,
                        s_addr,
                        e_addr,
                        event_type=event_key,
                        output_dir=output_dir,
                    )
                else:
                    print(f"  [{label}] 0x{addr:X} 附近未找到有效JSON，保存调试数据...")
                    try:
                        debug_data = pm.read_bytes(addr, 2048)
                        save_debug_hex(
                            room_id,
                            scan_idx,
                            pattern_str,
                            addr,
                            debug_data,
                            "event_anchor_forward_2048",
                            output_dir,
                        )
                    except Exception:
                        pass
        except Exception as e:
            print(f"  [{label}] 扫描出错: {e}")


def packet_callback(packet, pm_holder, scan_thread_holder, config):
    global current_room_id, scan_counter
    if not (packet.haslayer(TCP) and packet.haslayer(Raw)):
        return
    payload = packet[Raw].load
    if not payload:
        return
    matches = ROOM_ID_PATTERN.findall(payload)
    for room_id_bytes in matches:
        try:
            room_id = room_id_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if current_room_id == room_id:
            return
        current_room_id = room_id
        print(f"\n[网络] 捕获到新房间号: {room_id}")
        if scan_thread_holder[0] is not None:
            scan_stop_event.set()
            scan_thread_holder[0].join(timeout=2)
            scan_stop_event.clear()
        pm = pm_holder[0]
        if pm is None:
            print("错误：无法获取进程句柄")
            return
        with saved_lock:
            saved_json_hashes.clear()
        scan_counter = 0
        scan_thread = threading.Thread(
            target=scanning_worker,
            args=(
                pm,
                room_id,
                config["scan_duration"],
                config["scan_interval"],
                config["search_radius"],
                config["output_dir"],
            ),
            daemon=True,
        )
        scan_thread.start()
        scan_thread_holder[0] = scan_thread
        return


def start_network_listener(pm, config):
    filter_str = f"tcp and host {SERVER_IP} and port {SERVER_PORT}"
    print(f"[网络] 开始监听 | 过滤器: {filter_str}")
    print("[网络] 等待房间号... (Ctrl+C 退出)")
    pm_holder = [pm]
    scan_thread_holder = [None]
    try:
        sniff(
            filter=filter_str,
            prn=lambda pkt: packet_callback(pkt, pm_holder, scan_thread_holder, config),
            store=0,
        )
    except KeyboardInterrupt:
        print("\n[网络] 监听被用户中断。")
    finally:
        scan_stop_event.set()
        if scan_thread_holder[0]:
            scan_thread_holder[0].join(timeout=2)


def parse_args():
    parser = argparse.ArgumentParser(
        description="BidKing 对局信息自动化抓取工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python monitor_ram.py\n"
            "  python monitor_ram.py -d 600 -i 5 -r 16384 -o my_logs\n"
        ),
    )
    parser.add_argument(
        "-d",
        "--duration",
        type=int,
        default=DEFAULT_SCAN_DURATION,
        help=f"扫描持续时间（秒），默认 {DEFAULT_SCAN_DURATION}",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=int,
        default=DEFAULT_SCAN_INTERVAL,
        help=f"扫描间隔（秒），默认 {DEFAULT_SCAN_INTERVAL}",
    )
    parser.add_argument(
        "-r",
        "--radius",
        type=int,
        default=DEFAULT_SEARCH_RADIUS,
        help=f"JSON搜索半径（字节），默认 {DEFAULT_SEARCH_RADIUS}",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录，默认 {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--server-ip",
        type=str,
        default=SERVER_IP,
        help=f"服务器IP，默认 {SERVER_IP}",
    )
    parser.add_argument(
        "--server-port",
        type=int,
        default=SERVER_PORT,
        help=f"服务器端口，默认 {SERVER_PORT}",
    )
    return parser.parse_args()


def main():
    global SERVER_IP, SERVER_PORT

    args = parse_args()
    SERVER_IP = args.server_ip
    SERVER_PORT = args.server_port

    config = {
        "scan_duration": args.duration,
        "scan_interval": args.interval,
        "search_radius": args.radius,
        "output_dir": args.output,
    }

    print("=" * 60)
    print("  BidKing 对局信息自动化抓取工具")
    print("=" * 60)
    print(f"  进程名: {PROCESS_NAME}")
    print(f"  服务器: {SERVER_IP}:{SERVER_PORT}")
    print(f"  扫描时长: {config['scan_duration']}s")
    print(f"  扫描间隔: {config['scan_interval']}s")
    print(f"  搜索半径: {config['search_radius']} bytes")
    print(f"  输出目录: {config['output_dir']}")
    print(f"  事件类型: {', '.join(e['label'] for e in EVENT_TYPES.values())}")
    print("=" * 60)

    try:
        pm = pymem.Pymem(PROCESS_NAME)
        print(f"[进程] 已附加到 {PROCESS_NAME}, PID: {pm.process_id}")
    except pymem.exception.ProcessNotFound:
        print(f"[错误] 找不到进程 {PROCESS_NAME}，请确保游戏已运行。")
        sys.exit(1)
    except Exception as e:
        print(f"[错误] 附加进程失败: {e}")
        sys.exit(1)

    start_network_listener(pm, config)


if __name__ == "__main__":
    main()
