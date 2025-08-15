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

# --- 优选 IP 的 API 地址 ---
# 为“全网默认”和“三网优化”分别设置 API，以便未来使用不同源
IP_API_URL_DEFAULT = 'https://ipdb.api.030101.xyz/?type=bestcf&country=true'
IP_API_URL_ISP = 'https://ipdb.api.030101.xyz/?type=bestcf&country=true'

# --- 定义线路 ---
# 将“全网默认”和“三网优化”线路分开定义
DEFAULT_LINE = {"全网默认": "default"}
ISP_LINES = {
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
        print("错误: 缺少华为云 AK, SK 或 Project ID。")
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

def get_preferred_ips(api_url):
    """从指定 API 获取优选 IP 列表"""
    print(f"正在从 {api_url} 获取优选 IP...")
    try:
        response = requests.get(api_url, timeout=10)
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
                print(f"根据 MAX_IPS 设置，将使用前 {max_ips_count} 个 IP。")
                return valid_ips[:max_ips_count]
        
        return valid_ips
    except requests.RequestException as e:
        print(f"错误: 请求优选 IP 时发生错误: {e}")
        return []

def get_existing_dns_records(line_code):
    """获取指定线路下，当前域名已有的 A 记录"""
    print(f"正在查询域名 {DOMAIN_NAME} (线路: {line_code}) 的现有 DNS A 记录...")
    try:
        request = ListRecordSetsByZoneRequest(zone_id=zone_id)
        request.name = DOMAIN_NAME + "."
        request.type = "A"
        request.line = line_code
        response = dns_client.list_record_sets_by_zone(request)
        print(f"查询到 {len(response.recordsets)} 条已存在的 A 记录。")
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

def create_default_record_set(ip_list):
    """为“全网默认”线路创建解析记录集"""
    print("准备为 '全网默认' 线路创建一个解析记录集中...")
    try:
        # --- 最终修正点 ---
        # 使用正确的类 `CreateRecordSetReq` 来构建标准解析记录
        recordset_body = CreateRecordSetReq(
            name=DOMAIN_NAME + ".", 
            type="A", 
            records=ip_list, 
            ttl=60
        )
        body = CreateRecordSetRequestBody(recordset=recordset_body)
        request = CreateRecordSetRequest(zone_id=zone_id, body=body)
        dns_client.create_record_set(request)
        print(f"成功为 {DOMAIN_NAME} (全网默认) 创建了 A 记录。")
        return True
    except exceptions.ClientRequestException as e:
        print(f"错误: 创建默认解析记录集时失败: {e}")
        if hasattr(e, 'error_msg'): print(f"API 返回信息: {e.error_msg}")
        return False

def create_isp_record_set(ip_list, line_code):
    """为运营商线路创建解析记录集"""
    print(f"准备将 IP 创建到线路 '{line_code}' 的一个解析记录集中...")
    try:
        body = CreateRecordSetWithLineRequestBody(
            name=DOMAIN_NAME + ".", 
            type="A", 
            records=ip_list, 
            ttl=60,
            line=line_code
        )
        request = CreateRecordSetWithLineRequest(zone_id=zone_id, body=body)
        dns_client.create_record_set_with_line(request)
        print(f"成功为 {DOMAIN_NAME} (线路: {line_code}) 创建了 A 记录。")
        return True
    except exceptions.ClientRequestException as e:
        print(f"错误: 创建运营商解析记录集时失败: {e}")
        if hasattr(e, 'error_msg'): print(f"API 返回信息: {e.error_msg}")
        return False

def update_default_records():
    """更新“全网默认”线路的解析记录"""
    print("\n--- 开始处理“全网默认”线路 ---")
    new_ips = get_preferred_ips(IP_API_URL_DEFAULT)
    if not new_ips:
        print("未能获取“全网默认”的 IP 地址，跳过处理。")
        return

    line_name, line_code = list(DEFAULT_LINE.items())[0]
    existing_records = get_existing_dns_records(line_code)
    for record in existing_records:
        delete_dns_record(record.id)
    
    create_default_record_set(new_ips)

def update_isp_records():
    """更新“三网优化”线路的解析记录"""
    print("\n--- 开始处理“三网优化”线路 ---")
    new_ips = get_preferred_ips(IP_API_URL_ISP)
    if not new_ips:
        print("未能获取“三网优化”的 IP 地址，跳过处理。")
        return

    for line_name, line_code in ISP_LINES.items():
        print(f"\n--- 正在处理线路: {line_name} ({line_code}) ---")
        existing_records = get_existing_dns_records(line_code)
        for record in existing_records:
            delete_dns_record(record.id)
        create_isp_record_set(new_ips, line_code)

def main():
    """主执行函数"""
    print("--- 开始更新华为云优选 IP ---")
    
    if not DOMAIN_NAME:
        print("错误: 缺少 DOMAIN_NAME 环境变量。")
        return

    if not init_huawei_dns_client() or not get_zone_id():
        print("华为云客户端初始化或 Zone ID 获取失败，任务终止。")
        return

    # 1. 更新“全网默认”解析
    update_default_records()

    # 2. 更新“三网优化”解析
    update_isp_records()
    
    print("\n--- 所有线路更新完成 ---")

if __name__ == '__main__':
    main()
