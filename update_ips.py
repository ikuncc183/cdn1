# update_ips.py
import os
import requests
import json
import time

# 导入华为云 SDK 核心库
from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.exceptions import exceptions
# 导入华为云 DNS 服务库
from huaweicloudsdkdns.v2 import *
from huaweicloudsdkdns.v2.region.dns_region import DnsRegion

# --- 从 GitHub Secrets 读取配置 ---
HUAWEI_CLOUD_AK = os.environ.get('HUAWEI_CLOUD_AK')
HUAWEI_CLOUD_SK = os.environ.get('HUAWEI_CLOUD_SK')
HUAWEI_CLOUD_PROJECT_ID = os.environ.get('HUAWEI_CLOUD_PROJECT_ID')
HUAWEI_CLOUD_ZONE_NAME = os.environ.get('HUAWEI_CLOUD_ZONE_NAME')
DOMAIN_NAME = os.environ.get('DOMAIN_NAME')
MAX_IPS = os.environ.get('MAX_IPS')

# --- 新的 API 地址和 CNAME 目标 ---
IP_API_URL = 'https://ss.skymail.bio/'
DEFAULT_CNAME_TARGET = 'cf.090227.xyz'

# --- API Key 到华为云线路代码的映射 ---
ISP_LINE_MAP = {
    "mobile": "chinamobile",
    "unicom": "chinaunicom",
    "telecom": "chinatelecom"
}

# --- 全局变量 ---
dns_client = None
zone_id = None

def init_huawei_dns_client():
    """初始化华为云 DNS 客户端"""
    global dns_client
    if not all([HUAWEI_CLOUD_AK, HUAWEI_CLOUD_SK, HUAWEI_CLOUD_PROJECT_ID]):
        print("错误: 缺少华为云 AK, SK 或 Project ID，请检查 GitHub Secrets 配置。")
        return False
    
    credentials = BasicCredentials(ak=HUAWEI_CLOUD_AK, sk=HUAWEI_CLOUD_SK, project_id=HUAWEI_CLOUD_PROJECT_ID)
    
    try:
        dns_client = DnsClient.new_builder() \
            .with_credentials(credentials) \
            .with_region(DnsRegion.value_of("cn-east-3")) \
            .build()
        print("华为云 DNS 客户端初始化成功。")
        return True
    except Exception as e:
        print(f"错误: 初始化华为云 DNS 客户端失败: {e}")
        return False

def get_zone_id():
    """根据 Zone Name 获取 Zone ID"""
    global zone_id
    if not HUAWEI_CLOUD_ZONE_NAME:
        print("错误: 未配置 HUAWEI_CLOUD_ZONE_NAME。")
        return False
        
    print(f"正在查询公网域名 '{HUAWEI_CLOUD_ZONE_NAME}' 的 Zone ID...")
    try:
        request = ListPublicZonesRequest()
        response = dns_client.list_public_zones(request)
        for z in response.zones:
            if z.name == HUAWEI_CLOUD_ZONE_NAME + ".":
                zone_id = z.id
                print(f"成功找到 Zone ID: {zone_id}")
                return True
        print(f"错误: 未能找到名为 '{HUAWEI_CLOUD_ZONE_NAME}' 的公网域名。")
        return False
    except exceptions.ClientRequestException as e:
        print(f"错误: 查询 Zone ID 时发生 API 错误: {e}")
        return False

def get_ips_from_new_api():
    """从新的 JSON API 获取按运营商分类的 IP 字典"""
    print(f"正在从 {IP_API_URL} 获取优选 IP...")
    try:
        response = requests.get(IP_API_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        print("成功从新 API 获取并解析了 JSON 数据。")
        return data
    except requests.RequestException as e:
        print(f"错误: 请求优选 IP 时发生错误: {e}")
    except json.JSONDecodeError:
        print("错误: 解析 API 返回的 JSON 数据失败。")
    return None

def get_existing_records(line_code, record_type):
    """获取指定线路和类型的现有记录"""
    print(f"正在查询域名 {DOMAIN_NAME} (线路: {line_code}, 类型: {record_type}) 的现有记录...")
    try:
        request = ListRecordSetsByZoneRequest(zone_id=zone_id, name=DOMAIN_NAME + ".", type=record_type)
        request.line = line_code
        response = dns_client.list_record_sets_by_zone(request)
        print(f"查询到 {len(response.recordsets)} 条已存在的 {record_type} 记录。")
        return response.recordsets
    except exceptions.ClientRequestException as e:
        print(f"错误: 查询 DNS 记录时发生错误: {e}")
        return []

def delete_dns_record(record_id):
    """删除指定的 DNS 记录"""
    try:
        request = DeleteRecordSetRequest(zone_id=zone_id, recordset_id=record_id)
        dns_client.delete_record_set(request)
        print(f"成功删除记录: {record_id}")
        return True
    except exceptions.ClientRequestException as e:
        print(f"错误: 删除记录 {record_id} 时失败: {e}")
        return False

def create_a_record_set(ip_list, line_code):
    """为指定线路创建 A 记录集"""
    if not ip_list:
        print(f"线路 '{line_code}' 的 IP 列表为空，跳过创建。")
        return False
    print(f"准备将 {len(ip_list)} 个 IP 创建到线路 '{line_code}'...")
    try:
        body = CreateRecordSetRequestBody(name=DOMAIN_NAME + ".", type="A", records=ip_list, ttl=60)
        body.line = line_code
        request = CreateRecordSetRequest(zone_id=zone_id, body=body)
        dns_client.create_record_set(request)
        print(f"成功为 {DOMAIN_NAME} (线路: {line_code}) 创建了 A 记录。")
        return True
    except exceptions.ClientRequestException as e:
        print(f"错误: 创建 A 记录集时失败: {e}")
        return False

def create_cname_record_set(target):
    """为默认线路创建 CNAME 记录"""
    print(f"准备为默认线路创建 CNAME 记录指向 {target}...")
    try:
        body = CreateRecordSetRequestBody(name=DOMAIN_NAME + ".", type="CNAME", records=[target + "."], ttl=60)
        body.line = "default"
        request = CreateRecordSetRequest(zone_id=zone_id, body=body)
        dns_client.create_record_set(request)
        print(f"成功为 {DOMAIN_NAME} (默认线路) 创建了 CNAME 记录。")
        return True
    except exceptions.ClientRequestException as e:
        print(f"错误: 创建 CNAME 记录时失败: {e}")
        return False

def main():
    """主执行函数"""
    print("--- 开始更新华为云优选 IP (分线路版) ---")
    
    if not init_huawei_dns_client() or not get_zone_id():
        print("华为云客户端初始化或 Zone ID 获取失败，任务终止。")
        return

    all_ips_by_isp = get_ips_from_new_api()
    if not all_ips_by_isp:
        print("未能获取优选 IP 数据，本次任务终止。")
        return

    # --- 1. 处理默认线路 (CNAME) ---
    print("\n--- 正在处理: 默认线路 (CNAME) ---")
    # 删除旧的 A 记录和 CNAME 记录
    for record in get_existing_records("default", "A"):
        delete_dns_record(record.id)
    for record in get_existing_records("default", "CNAME"):
        delete_dns_record(record.id)
    # 创建新的 CNAME 记录
    create_cname_record_set(DEFAULT_CNAME_TARGET)

    # --- 2. 遍历处理运营商线路 (A 记录) ---
    for api_key, line_code in ISP_LINE_MAP.items():
        print(f"\n--- 正在处理: {api_key} 线路 ({line_code}) ---")
        
        ip_list = all_ips_by_isp.get(api_key, [])
        if not ip_list:
            print(f"API 数据中未找到 '{api_key}' 的 IP 列表，跳过此线路。")
            continue

        # 应用 MAX_IPS 限制
        if MAX_IPS and MAX_IPS.isdigit():
            max_count = int(MAX_IPS)
            if 0 < max_count < len(ip_list):
                print(f"根据 MAX_IPS={max_count} 的设置，将只使用前 {max_count} 个 IP。")
                ip_list = ip_list[:max_count]

        # 删除该线路下的旧 A 记录
        for record in get_existing_records(line_code, "A"):
            delete_dns_record(record.id)
        
        # 创建新的 A 记录集
        create_a_record_set(ip_list, line_code)

    print("\n--- 所有线路更新完成 ---")

if __name__ == '__main__':
    main()
