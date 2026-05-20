#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内存自动扫描程序
功能：监听网络获取房间号，然后每隔3秒扫描游戏进程内存中的指定字符串，
记录地址及前后512/1024字节数据，持续5分钟。
新增：结构体识别，匹配特定格式的UTF-16LE字符串。
"""

import re
import time
import threading
import os
import sys
from datetime import datetime
from typing import List, Set, Dict, Tuple, Optional

import pymem
import pymem.pattern
from scapy.all import sniff, IP, TCP, Raw

# ========== 配置 ==========
PROCESS_NAME = "BidKing.exe"
SERVER_IP = "203.107.63.169"   # 根据实际填写
SERVER_PORT = 10000
ROOM_ID_PATTERN = re.compile(rb'\b(\d{2,5}:\d{12,20})\b')
SCAN_INTERVAL = 3   # 秒
SCAN_DURATION = 5 * 60  # 5分钟
OUTPUT_DIR = "logs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 需要搜索的字符串列表（可以自定义，房间号会自动加入）
SEARCH_STRINGS = [
    # "GameData",
    # 可以添加更多关键字
]

# 结构体模式（UTF-16LE编码，原始字节模式）
# 原始字符串: (uid:数字, itemId:数字, Size:[数字,数字], rank:数字, pos:数字, sizeCount:数字)
# 转换为UTF-16LE后，每个字符间有0x00，因此正则需适配。
# 例如 '(' -> b'(\x00', 'u'->b'u\x00' ...
# 为简化，我们先用固定特征搜索 "(uid:" 的UTF-16LE形式，再验证。
STRUCT_FIXED_PATTERN = b'(\x00u\x00i\x00d\x00:\x00'   # "(uid:" 的 UTF-16LE
STRUCT_FULL_REGEX = re.compile(
    rb'\(\x00u\x00i\x00d\x00:\x00(\d+)\x00,\x00i\x00t\x00e\x00m\x00I\x00d\x00:\x00(\d+)\x00,'
    rb'\x00S\x00i\x00z\x00e\x00:\x00\[\x00(\d+)\x00,\x00(\d+)\x00\]\x00,'
    rb'\x00r\x00a\x00n\x00k\x00:\x00(\d+)\x00,'
    rb'\x00p\x00o\x00s\x00:\x00(\d+)\x00,'
    rb'\x00s\x00i\x00z\x00e\x00C\x00o\x00u\x00n\x00t\x00:\x00(\d+)\x00\)\x00'
)

# 全局变量
current_room_id = None
scan_stop_event = threading.Event()
scan_counter = 0

# ========== 内存扫描函数（原有） ==========
def scan_memory(pm: pymem.Pymem, search_strings: List[str]) -> dict:
    """扫描进程内存，返回所有匹配结果。"""
    results = {}
    for s in search_strings:
        if not s:
            continue
        try:
            pattern = s.encode('utf-8')
            matches = pymem.pattern.pattern_scan_all(pm.process_handle, pattern, return_multiple=True)
            if matches:
                addr_list = []
                for addr in matches:
                    start_before = max(0, addr - 512)
                    size_before = addr - start_before
                    before_bytes = pm.read_bytes(start_before, size_before) if size_before > 0 else b''
                    after_bytes = pm.read_bytes(addr, 1024) if addr < 0x7fffffffffff else b''
                    addr_list.append((addr, before_bytes, after_bytes))
                results[s] = addr_list
        except Exception as e:
            print(f"扫描字符串 '{s}' 时出错: {e}")
    return results

def format_context(offset_desc: str, data: bytes) -> str:
    """将字节数据格式化为十六进制+ASCII，每行16字节"""
    if not data:
        return f"{offset_desc}: (空)\n"
    lines = [f"{offset_desc}:"]
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part = ' '.join(f'{b:02x}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append(f"  {i:04x}: {hex_part:<48} {ascii_part}")
    return '\n'.join(lines)

def log_scan_results(scan_idx: int, room_id: str, results: dict):
    """将扫描结果写入日志文件"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(OUTPUT_DIR, f"scan_{scan_idx:03d}_{timestamp}.txt")
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"扫描次数: {scan_idx}\n")
        f.write(f"房间号: {room_id}\n")
        f.write(f"扫描时间: {datetime.now().isoformat()}\n")
        f.write("=" * 80 + "\n\n")
        for s, addr_list in results.items():
            f.write(f"字符串: {s}\n")
            f.write(f"匹配数量: {len(addr_list)}\n")
            for i, (addr, before, after) in enumerate(addr_list):
                f.write(f"  匹配 #{i+1}: 地址 0x{addr:X}\n")
                f.write(format_context("  前512字节", before))
                f.write(format_context("  后1024字节", after))
                f.write("\n")
            f.write("-" * 80 + "\n")
    print(f"扫描 #{scan_idx} 结果已保存到 {filename}")

# ========== 新增：结构体识别 ==========
def extract_struct_fields(matched_bytes: bytes) -> Optional[Dict[str, int]]:
    """从匹配的UTF-16LE字节串中提取字段值，返回字典或None"""
    # 注意：matched_bytes可能包含额外的前后缀，但正则已确保完整匹配
    m = STRUCT_FULL_REGEX.search(matched_bytes)
    if not m:
        return None
    groups = m.groups()
    if len(groups) != 7:
        return None
    try:
        return {
            'uid': int(groups[0].decode()),
            'itemId': int(groups[1].decode()),
            'size0': int(groups[2].decode()),
            'size1': int(groups[3].decode()),
            'rank': int(groups[4].decode()),
            'pos': int(groups[5].decode()),
            'sizeCount': int(groups[6].decode())
        }
    except (ValueError, UnicodeDecodeError):
        return None
        
# ========== 新增：房间号后缀扫描 ==========
def scan_room_id_suffix(pm: pymem.Pymem, suffix: str) -> List[Tuple[int, bytes, bytes]]:
    """
    扫描内存中指定数字字符串的UTF-16LE编码。
    参数 suffix: 15位数字字符串（房间号后半段）
    返回列表，每个元素为 (address, context_before, context_after)
    context_before: 地址前512字节
    context_after: 地址后1024字节
    """
    if not suffix or not suffix.isdigit():
        return []
    # 构建UTF-16LE编码：每个数字字符后加\x00，结尾不加额外的\x00（因为字符串可能以\x00\x00结尾）
    # 例如 '123' -> b'1\x002\x003\x00'
    pattern = b''.join(ch.encode('utf-16le') for ch in suffix)
    results = []
    try:
        matches = pymem.pattern.pattern_scan_all(pm.process_handle, pattern, return_multiple=True)
        for addr in matches:
            start_before = max(0, addr - 512)
            size_before = addr - start_before
            before_bytes = pm.read_bytes(start_before, size_before) if size_before > 0 else b''
            after_bytes = pm.read_bytes(addr, 4096) if addr < 0x7fffffffffff else b''
            results.append((addr, before_bytes, after_bytes))
    except Exception as e:
        print(f"扫描房间号后缀时出错: {e}")
    return results

def log_room_id_suffix_results(scan_idx: int, room_id: str, suffix: str, matches: List[Tuple[int, bytes, bytes]]):
    """将房间号后缀扫描结果写入日志文件"""
    if not matches:
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(OUTPUT_DIR, f"room_suffix_scan_{scan_idx:03d}_{timestamp}.txt")
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"房间号后缀扫描次数: {scan_idx}\n")
        f.write(f"房间号: {room_id}\n")
        f.write(f"后缀: {suffix}\n")
        f.write(f"扫描时间: {datetime.now().isoformat()}\n")
        f.write("=" * 80 + "\n\n")
        for i, (addr, before, after) in enumerate(matches):
            f.write(f"匹配 #{i+1}: 地址 0x{addr:X}\n")
            f.write(format_context("  前512字节", before))
            f.write(format_context("  后4096字节", after))
            f.write("\n" + "-" * 80 + "\n")
    print(f"房间号后缀扫描 #{scan_idx} 结果已保存到 {filename}")

def scan_struct_pattern(pm: pymem.Pymem) -> List[Tuple[int, bytes, bytes, Dict[str, int]]]:
    """
    扫描内存中符合结构体格式的UTF-16LE字符串。
    返回列表，每个元素为 (address, context_before, context_after, fields_dict)
    context_before: 地址前512字节
    context_after: 地址后1024字节
    """
    results = []
    try:
        # 第一步：搜索固定特征 "(uid:" 的UTF-16LE形式
        # 注意：pattern_scan_all 需要的是字节串，不能有通配符
        matches = pymem.pattern.pattern_scan_all(pm.process_handle, STRUCT_FIXED_PATTERN, return_multiple=True)
        for addr in matches:
            # 读取从 addr 开始的 256 字节（因为完整字符串一般不会超过这个长度）
            try:
                data = pm.read_bytes(addr, 256)
            except:
                continue
            # 尝试用完整正则匹配
            if STRUCT_FULL_REGEX.search(data):
                # 找到完整匹配，确定实际结束位置
                # 为了获取准确的 before/after，需要知道字符串的结束地址
                # 简单起见，我们读取从 addr 开始的 512 字节作为前后文，
                # 但 before 需要从 addr - 512 开始。
                start_before = max(0, addr - 512)
                size_before = addr - start_before
                before_bytes = pm.read_bytes(start_before, size_before) if size_before > 0 else b''
                # 后1024字节从 addr 开始读取（但要注意字符串本身已包含在 data 中，后1024字节包含它）
                after_bytes = pm.read_bytes(addr, 1024) if addr < 0x7fffffffffff else b''
                fields = extract_struct_fields(data)
                if fields:
                    results.append((addr, before_bytes, after_bytes, fields))
    except Exception as e:
        print(f"扫描结构体时出错: {e}")
    return results

def log_struct_results(scan_idx: int, room_id: str, struct_matches: List[Tuple[int, bytes, bytes, Dict[str, int]]]):
    """将结构体识别结果写入日志文件"""
    if not struct_matches:
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(OUTPUT_DIR, f"struct_scan_{scan_idx:03d}_{timestamp}.txt")
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"结构体扫描次数: {scan_idx}\n")
        f.write(f"房间号: {room_id}\n")
        f.write(f"扫描时间: {datetime.now().isoformat()}\n")
        f.write("=" * 80 + "\n\n")
        for i, (addr, before, after, fields) in enumerate(struct_matches):
            f.write(f"匹配 #{i+1}: 地址 0x{addr:X}\n")
            f.write(f"解析字段: {fields}\n")
            f.write(format_context("  前512字节", before))
            f.write(format_context("  后1024字节", after))
            f.write("\n" + "-" * 80 + "\n")
    print(f"结构体扫描 #{scan_idx} 结果已保存到 {filename}")



# ========== 修改扫描工作线程 ==========
def scanning_worker(pm: pymem.Pymem, room_id: str):
    """扫描工作线程，每隔SCAN_INTERVAL秒扫描一次，持续SCAN_DURATION秒"""
    global scan_counter
    start_time = time.time()
    scan_counter = 0
    # 提取房间号后半段（15位数字）
    room_suffix = ""
    if ':' in room_id:
        room_suffix = room_id.split(':', 1)[1]
        if len(room_suffix) != 15 or not room_suffix.isdigit():
            print(f"警告: 房间号后缀不是15位数字: {room_suffix}")
            room_suffix = ""
    # 原有字符串搜索列表（可保留，也可清空）
    search_strings = SEARCH_STRINGS.copy()
    if room_id:
        search_strings.append(room_id)
    search_strings = list(set(search_strings))
    print(f"开始扫描，共 {len(search_strings)} 个字符串项，房间号后缀: {room_suffix}，间隔 {SCAN_INTERVAL}s，持续时间 {SCAN_DURATION}s")
    
    while not scan_stop_event.is_set():
        elapsed = time.time() - start_time
        if elapsed > SCAN_DURATION:
            print("扫描时间已到，停止扫描。")
            break
        scan_counter += 1
        print(f"\n=== 第 {scan_counter} 次扫描 ===")
        # 原有字符串扫描
        try:
            results = scan_memory(pm, search_strings)
            if any(results.values()):
                log_scan_results(scan_counter, room_id, results)
            else:
                print("未找到任何匹配字符串。")
        except Exception as e:
            print(f"字符串扫描出错: {e}")
        # 新增：房间号后缀（15位数字 UTF-16LE）扫描
        if room_suffix:
            try:
                suffix_matches = scan_room_id_suffix(pm, room_suffix)
                if suffix_matches:
                    log_room_id_suffix_results(scan_counter, room_id, room_suffix, suffix_matches)
                else:
                    print("未找到匹配的房间号后缀。")
            except Exception as e:
                print(f"房间号后缀扫描出错: {e}")
        # 等待下一次扫描
        for _ in range(SCAN_INTERVAL):
            if scan_stop_event.is_set():
                break
            time.sleep(1)

# ========== 网络监听部分（保持不变） ==========
def packet_callback(packet, pm_holder, scan_thread_holder, room_id_holder):
    """处理捕获到的TCP包，提取房间号"""
    global current_room_id, scan_counter
    if packet.haslayer(TCP) and packet.haslayer(Raw):
        payload = packet[Raw].load
        if not payload:
            return
        matches = ROOM_ID_PATTERN.findall(payload)
        for room_id_bytes in matches:
            try:
                room_id = room_id_bytes.decode('utf-8')
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
            scan_thread = threading.Thread(target=scanning_worker, args=(pm, room_id), daemon=True)
            scan_thread.start()
            scan_thread_holder[0] = scan_thread
            return

def start_network_listener(pm):
    """启动网络监听，等待房间号"""
    filter_str = f"tcp and host {SERVER_IP} and port {SERVER_PORT}"
    print(f"开始网络监听，过滤器: {filter_str}")
    print("等待房间号...")
    pm_holder = [pm]
    scan_thread_holder = [None]
    room_id_holder = [None]
    try:
        sniff(filter=filter_str, prn=lambda pkt: packet_callback(pkt, pm_holder, scan_thread_holder, room_id_holder), store=0)
    except KeyboardInterrupt:
        print("\n监听被用户中断。")
    finally:
        scan_stop_event.set()
        if scan_thread_holder[0]:
            scan_thread_holder[0].join(timeout=2)

# ========== 主程序 ==========
def main():
    try:
        pm = pymem.Pymem(PROCESS_NAME)
        print(f"已附加到进程 {PROCESS_NAME}, 进程ID: {pm.process_id}")
    except pymem.exception.ProcessNotFound:
        print(f"找不到进程 {PROCESS_NAME}，请确保游戏已运行。")
        sys.exit(1)
    except Exception as e:
        print(f"附加进程失败: {e}")
        sys.exit(1)

    start_network_listener(pm)

if __name__ == "__main__":
    main()