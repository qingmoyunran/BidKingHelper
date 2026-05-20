#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
竞拍之王 - 房间号自动抓取脚本
用途：实时捕获网络包中的房间号字符串，并输出到控制台/文件。
依赖：scapy, Npcap/WinPcap
运行：需要管理员/root权限
"""

import re
from scapy.all import sniff, IP, TCP, Raw
import threading
import time

# ========== 配置区域 ==========
# 游戏服务器的 IP 和端口（根据你的抓包结果填写）
SERVER_IP = "203.107.63.169"    # 请替换为实际游戏服务器IP
SERVER_PORT = 10000              # 请替换为实际端口

# 房间号正则（根据样本 "2201:961935625930095" 设计）
# 说明：4位数字 + 冒号 + 15位数字；为兼容其他格式，可放宽为 2~5 位数字冒号 12~20 位数字
ROOM_ID_PATTERN = re.compile(rb'\b(\d{2,5}:\d{12,20})\b')

# 输出文件（可选，留空则不写入文件）
OUTPUT_FILE = "room_ids.txt"

# 去重记录
seen_rooms = set()

# ========== 回调函数 ==========
def packet_callback(packet):
    """每个 TCP 包到达时调用"""
    # 只处理含有 TCP 原始负载的包
    if not packet.haslayer(TCP) or not packet.haslayer(Raw):
        return
    
    # 可选：增加 IP 过滤（避免抓取其他无关流量）
    if IP in packet:
        ip_src = packet[IP].src
        ip_dst = packet[IP].dst
        if not (ip_src == SERVER_IP or ip_dst == SERVER_IP):
            return
        # 可选：检查端口
        tcp_layer = packet[TCP]
        if not (tcp_layer.sport == SERVER_PORT or tcp_layer.dport == SERVER_PORT):
            return

    payload = packet[Raw].load
    # 用正则查找所有匹配的房间号
    matches = ROOM_ID_PATTERN.findall(payload)
    for room_id_bytes in matches:
        try:
            room_id = room_id_bytes.decode('utf-8')
        except UnicodeDecodeError:
            continue
        if room_id not in seen_rooms:
            seen_rooms.add(room_id)
            print(f"[新房间号] {room_id}  时间: {packet.time}")
            if OUTPUT_FILE:
                with open(OUTPUT_FILE, 'a', encoding='utf-8') as f:
                    f.write(f"{room_id}\n")
            # 这里可以调用其他函数，例如写入共享内存、触发内存扫描等

# ========== 主程序 ==========
def main():
    # 构造 BPF 过滤器（提高性能，避免抓取大量无关包）
    filter_str = f"tcp and (host {SERVER_IP}) and (port {SERVER_PORT})"
    print(f"开始抓包，过滤器: {filter_str}")
    print("按 Q 回车退出...")
    stop_event = threading.Event()
    
    def capture():
        sniff(filter=filter_str, prn=packet_callback, store=0, stop_filter=lambda p: stop_event.is_set())
    
    try:
        # sniff 参数说明：
        # filter: BPF过滤规则
        # prn: 回调函数
        # store=0: 不存储原始包（节省内存）
        # count=0: 无限抓取
        t = threading.Thread(target=capture, daemon=True)
        t.start()
        
        while not stop_event.is_set():
            if input().strip().lower() == 'q':
                stop_event.set()
                print("正在停止抓包...")
                break


    except PermissionError:
        print("权限不足！请以管理员/root权限运行此脚本。")
    except Exception as e:
        print(f"发生错误: {e}")

if __name__ == "__main__":
    main()