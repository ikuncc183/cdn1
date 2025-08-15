# update_ips.py (Final Version - v4 with configurable TTL)
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
# (可选) DNS 解析记录的 TTL 值
DNS_TTL = os.environ.get('DNS_TTL')

# --- 优选 IP 的 API 地址 ---
IP_API_URL = 'https://ipdb.api.030101.xyz/?type=bestcf&country=true'

# --- 定义运营商线路 ---
ISP_LINES = {
    "默认": "default",
    "移动": "Yidong",
    "电信": "Dianxin",
    "联通": "Liantong"
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

def get_preferred_ips():
    """从 API 获取优选 IP 列表"""
    print(f"正在从 {IP_API_URL} 获取优选 IP...")
    try:
        response = requests.get(IP_API_URL, timeout=15)
        response.raise_for_status()
        lines = response.text.strip().split('\n')
        valid_ips = [line.split('#')[0].strip() for line in lines if line.strip() and not line.startswith('#')]

        if not valid_ips:
            print("警告: 从 API 获取到的内容为空或无效。")
            return []
        
        print(f"成功获取并解析了 {len(valid_ips)} 个优选 IP。")

        if MAX_IPS and MAX_IPS.isdigit():
            max_ips_count = int(MAX_IPS)
            if 0 < max_ips_count < len(valid_ips):
                print(f"根据 MAX_IPS={max_ips_count} 的设置，将只使用前 {max_ips_count} 个 IP。")
                return valid_ips[:max_ips_count]
        
        return valid_ips
    except requests.RequestException as e:
        print(f"错误: 请求优选 IP 时发生错误: {e}")
        return []

def get_existing_dns_records(line_code):
    """获取指定线路下，当前域名已有的 A 记录"""
    friendly_name = "默认 (default)" if line_code == "default" else line_code
    print(f"正在查询域名 {DOMAIN_NAME} (线路: {friendly_name}) 的现有 DNS A 记录...")
    try:
        if line_code != "default":
            request = ListRecordSetsByZoneRequest(zone_id=zone_id)
            request.name = DOMAIN_NAME + "."
            request.type = "A"
            request.line = line_code
            response = dns_client.list_record_sets_by_zone(request)
        else:
            # 关键修正: ListRecordSetsRequest 不接受初始化参数, 需在创建后赋值
            request_no_line = ListRecordSetsRequest()
            request_no_line.zone_id = zone_id
            request_no_line.name = DOMAIN_NAME + "."
            request_no_line.type = "A"
            response = dns_client.list_record_sets(request_no_line)

        print(f"查询到 {len(response.recordsets)} 条已存在的 A 记录。")
        return response.recordsets
    except exceptions.ClientRequestException as e:
        if hasattr(e, 'status_code') and e.status_code == 404:
            print("查询到 0 条已存在的 A 记录。")
            return []
        print(f"错误: 查询 DNS 记录时发生错误: {e}")
        return []

def create_record_set(ip_list, line_code, ttl):
    """根据线路创建新的解析记录集 (默认或带线路)"""
    try:
        if line_code == "default":
            print(f"准备为 '全网默认' 创建一个新的解析记录集 (TTL: {ttl})...")
            body = CreateRecordSetRequestBody(name=DOMAIN_NAME + ".", type="A", records=ip_list, ttl=ttl)
            request = CreateRecordSetRequest(zone_id=zone_id, body=body)
            dns_client.create_record_set(request)
        else:
            print(f"准备为线路 '{line_code}' 创建一个新的解析记录集 (TTL: {ttl})...")
            body = CreateRecordSetWithLineRequestBody(name=DOMAIN_NAME + ".", type="A", records=ip_list, ttl=ttl, line=line_code)
            request = CreateRecordSetWithLineRequest(zone_id=zone_id, body=body)
            dns_client.create_record_set_with_line(request)
        
        print(f"成功为 {DOMAIN_NAME} (线路: {line_code}) 创建了包含 {len(ip_list)} 个 IP 的 A 记录。")
        return True
    except exceptions.ClientRequestException as e:
        print(f"错误: 创建解析记录集时失败: {e}")
        if hasattr(e, 'error_msg'): print(f"API 返回信息: {e.error_msg}")
        return False

def update_record_set(record_id, ip_list, line_code, ttl):
    """根据线路更新已有的解析记录集 (默认或带线路)"""
    try:
        # 更新操作对于默认和带线路的记录是统一的
        print(f"准备更新线路 '{line_code}' 的解析记录 (ID: {record_id}, TTL: {ttl})...")
        body = UpdateRecordSetReq(records=ip_list, ttl=ttl)
        request = UpdateRecordSetRequest(zone_id=zone_id, recordset_id=record_id, body=body)
        dns_client.update_record_set(request)

        print(f"成功为 {DOMAIN_NAME} (线路: {line_code}) 更新了包含 {len(ip_list)} 个 IP 的 A 记录。")
        return True
    except exceptions.ClientRequestException as e:
        print(f"错误: 更新解析记录集时失败: {e}")
        if hasattr(e, 'error_msg'): print(f"API 返回信息: {e.error_msg}")
        return False

def main():
    """主执行函数"""
    print("--- 开始更新华为云优选 IP ---")
    
    if not DOMAIN_NAME:
        print("错误: 缺少必要的 DOMAIN_NAME 环境变量。")
        return

    if not init_huawei_dns_client() or not get_zone_id():
        print("华为云客户端初始化或 Zone ID 获取失败，任务终止。")
        return

    new_ips = get_preferred_ips()
    if not new_ips:
        print("未能获取新的 IP 地址，本次任务终止。")
        return

    # --- TTL 配置处理 ---
    try:
        # 尝试将 TTL 转换为整数，华为云 DNS TTL 范围为 1-2147483647
        ttl_value = int(DNS_TTL)
        if not (1 <= ttl_value <= 2147483647):
            print(f"警告: 配置的 TTL 值 '{DNS_TTL}' 超出有效范围 (1-2147483647)，将使用默认值 60。")
            ttl_value = 60
        else:
            print(f"将使用配置的 TTL 值: {ttl_value}")
    except (ValueError, TypeError):
        print("未配置或配置的 TTL 值无效，将使用默认值 60。")
        ttl_value = 60

    for line_name, line_code in ISP_LINES.items():
        print(f"\n--- 正在处理线路: {line_name} ({line_code}) ---")

        existing_records = get_existing_dns_records(line_code)
        
        if existing_records:
            record_to_update = existing_records[0]
            print(f"记录已存在 (ID: {record_to_update.id})，准备执行更新操作。")
            update_record_set(record_to_update.id, new_ips, line_code, ttl_value)
        else:
            print("记录不存在，准备执行创建操作。")
            create_record_set(new_ips, line_code, ttl_value)
    
    print("\n--- 所有线路更新完成 ---")

if __name__ == '__main__':
    main()
