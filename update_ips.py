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
# 华为云访问密钥 ID (Access Key ID)
HUAWEI_CLOUD_AK = os.environ.get('HUAWEI_CLOUD_AK')
# 华为云秘密访问密钥 (Secret Access Key)
HUAWEI_CLOUD_SK = os.environ.get('HUAWEI_CLOUD_SK')
# 华为云 Project ID
HUAWEI_CLOUD_PROJECT_ID = os.environ.get('HUAWEI_CLOUD_PROJECT_ID')
# 华为云托管的公网域名 (Zone Name)
HUAWEI_CLOUD_ZONE_NAME = os.environ.get('HUAWEI_CLOUD_ZONE_NAME')
# 需要更新解析的完整域名
DOMAIN_NAME = os.environ.get('DOMAIN_NAME')
# (可选) 需要解析的IP数量
MAX_IPS = os.environ.get('MAX_IPS')

# --- 优选 IP 的 API 地址 ---
IP_API_URL = 'https://ipdb.api.030101.xyz/?type=bestcf&country=true'

# --- 全局变量 ---
dns_client = None
zone_id = None

def init_huawei_dns_client():
    """初始化华为云 DNS 客户端"""
    global dns_client
    if not all([HUAWEI_CLOUD_AK, HUAWEI_CLOUD_SK, HUAWEI_CLOUD_PROJECT_ID]):
        print("错误: 缺少华为云 AK, SK 或 Project ID，请检查 GitHub Secrets 配置。")
        return False
    
    credentials = BasicCredentials(ak=HUAWEI_CLOUD_AK,
                                   sk=HUAWEI_CLOUD_SK,
                                   project_id=HUAWEI_CLOUD_PROJECT_ID)
    
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
    retry_count = 3
    retry_delay = 10
    for attempt in range(retry_count):
        try:
            response = requests.get(IP_API_URL, timeout=10)
            response.raise_for_status()
            lines = response.text.strip().split('\n')
            valid_ips = [line.split('#')[0].strip() for line in lines if line.strip()]

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
            if attempt < retry_count - 1:
                time.sleep(retry_delay)
            else:
                return []
    return []

def get_existing_dns_records():
    """获取当前域名已有的 A 记录 (华为云版)"""
    print(f"正在查询域名 {DOMAIN_NAME} 的现有 DNS A 记录...")
    try:
        request = ListRecordSetsByZoneRequest(zone_id=zone_id)
        response = dns_client.list_record_sets_by_zone(request)
        
        matching_records = []
        for record in response.recordsets:
            if record.name == DOMAIN_NAME + "." and record.type == "A":
                matching_records.append(record)

        print(f"查询到 {len(matching_records)} 条已存在的 A 记录。")
        return matching_records
    except exceptions.ClientRequestException as e:
        print(f"错误: 查询 DNS 记录时发生错误: {e}")
        return []

def delete_dns_record(record_id):
    """删除指定的 DNS 记录 (华为云版)"""
    try:
        request = DeleteRecordSetRequest(zone_id=zone_id, recordset_id=record_id)
        dns_client.delete_record_set(request)
        print(f"成功删除记录: {record_id}")
        return True
    except exceptions.ClientRequestException as e:
        print(f"错误: 删除记录 {record_id} 时失败: {e}")
        return False

def create_dns_record_set(ip_list):
    """将所有 IP 创建到一个解析记录集中 (华为云版)"""
    if not ip_list:
        print("IP 列表为空，无需创建记录。")
        return False
        
    print(f"准备将 {len(ip_list)} 个 IP 创建到一个解析记录集中...")
    try:
        # 核心修改：将所有 IP 放入 records 列表中，一次性创建
        body = CreateRecordSetRequestBody(name=DOMAIN_NAME + ".", type="A", records=ip_list, ttl=60)
        
        # 对于加权解析，权重是记录集(RecordSet)的属性，而不是单个IP的。
        # 如果需要，可以取消下面一行的注释来设置权重。默认为1。
        # body.weight = 1 
        
        request = CreateRecordSetRequest(zone_id=zone_id, body=body)
        dns_client.create_record_set(request)
        print(f"成功为 {DOMAIN_NAME} 创建了包含 {len(ip_list)} 个 IP 的 A 记录。")
        return True
    except exceptions.ClientRequestException as e:
        print(f"错误: 创建解析记录集时失败: {e}")
        return False

def main():
    """主执行函数"""
    print("--- 开始更新华为云优选 IP ---")
    
    if not all([DOMAIN_NAME]):
        print("错误: 缺少必要的 DOMAIN_NAME 环境变量。")
        return

    if not init_huawei_dns_client() or not get_zone_id():
        print("华为云客户端初始化或 Zone ID 获取失败，任务终止。")
        return

    new_ips = get_preferred_ips()
    if not new_ips:
        print("未能获取新的 IP 地址，本次任务终止。")
        return

    existing_records = get_existing_dns_records()
    if existing_records:
        print("\n--- 开始删除旧的 DNS 记录 ---")
        for record in existing_records:
            delete_dns_record(record.id)
    else:
        print("没有需要删除的旧记录。")

    print("\n--- 开始创建新的 DNS 记录 ---")
    # 核心修改：不再循环，而是调用新的函数一次性创建所有记录
    if create_dns_record_set(new_ips):
        print(f"\n--- 更新完成 ---")
    else:
        print(f"\n--- 更新失败 ---")


if __name__ == '__main__':
    main()
