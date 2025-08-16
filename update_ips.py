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
IP_API_URL = 'https://raw.githubusercontent.com/hubbylei/bestcf/main/bestcf.txt'

# --- 定义运营商线路 ---
# 核心修改点：将线路代码更新为华为云普遍接受的拼音格式
ISP_LINES = {
    "移动": "Yidong",
    "电信": "Dianxin",
    "联通": "Liantong"
}

# --- 全局变量 ---
dns_client = None
zone_id = None

def init_huawe_dns_client():
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
            # 华为云的 zone name 会自带一个点
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
            # 过滤掉注释和空行
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
            if attempt < retry_count - 1:
                print(f"将在 {retry_delay} 秒后重试...")
                time.sleep(retry_delay)
            else:
                print("已达到最大重试次数，获取 IP 失败。")
                return []
    return []

# 关键修改点: 增加分页处理逻辑
def get_all_existing_a_records():
    """获取当前域名已有的所有 A 记录 (处理分页)"""
    print(f"正在查询域名 {DOMAIN_NAME} 的所有现有 DNS A 记录...")
    all_records = []
    marker = None
    limit = 100  # 每次请求获取100条记录
    
    while True:
        try:
            request = ListRecordSetsByZoneRequest(zone_id=zone_id)
            request.name = DOMAIN_NAME + "."
            request.type = "A"
            request.limit = limit
            if marker:
                request.marker = marker
            
            response = dns_client.list_record_sets_by_zone(request)
            
            if response.recordsets:
                all_records.extend(response.recordsets)
            
            # 检查是否有下一页
            if response.links and response.links.next:
                # 从 next 链接中解析出 marker
                # 示例: "https://.../v2/zones/.../recordsets?marker=...&limit=100"
                # 我们需要提取 marker 的值
                next_link = response.links.next
                marker_param = "marker="
                if marker_param in next_link:
                    marker = next_link.split(marker_param)[1].split('&')[0]
                else:
                    break # 如果 next 链接里没有 marker，则结束
            else:
                # 如果没有 next 链接，说明是最后一页
                break
                
        except exceptions.ClientRequestException as e:
            print(f"错误: 查询 DNS 记录时发生错误: {e}")
            return [] # 出错时返回空列表

    print(f"通过分页查询，总共找到 {len(all_records)} 条已存在的 A 记录。")
    return all_records


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

def create_dns_record_set(ip_list, line_code):
    """将所有 IP 创建到指定线路的一个解析记录集中"""
    if not ip_list:
        print("IP 列表为空，无需创建记录。")
        return False
        
    print(f"准备将 {len(ip_list)} 个 IP 创建到线路 '{line_code}' 的一个解析记录集中...")
    try:
        # 使用正确的请求体类 CreateRecordSetWithLineRequestBody
        body = CreateRecordSetWithLineRequestBody(
            name=DOMAIN_NAME + ".", 
            type="A", 
            records=ip_list, 
            ttl=60,
            line=line_code
        )
        
        request = CreateRecordSetWithLineRequest(zone_id=zone_id, body=body)
        dns_client.create_record_set_with_line(request)

        print(f"成功为 {DOMAIN_NAME} (线路: {line_code}) 创建了包含 {len(ip_list)} 个 IP 的 A 记录。")
        return True
    except exceptions.ClientRequestException as e:
        print(f"错误: 创建解析记录集时失败: {e}")
        if hasattr(e, 'error_msg'):
            print(f"API 返回信息: {e.error_msg}")
        return False

def main():
    """主执行函数"""
    print("--- 开始更新华为云优选 IP ---")
    
    if not all([DOMAIN_NAME]):
        print("错误: 缺少必要的 DOMAIN_NAME 环境变量。")
        return

    if not init_huawe_dns_client() or not get_zone_id():
        print("华为云客户端初始化或 Zone ID 获取失败，任务终止。")
        return

    new_ips = get_preferred_ips()
    if not new_ips:
        print("未能获取新的 IP 地址，本次任务终止。")
        return

    # 主要逻辑变更: 先一次性获取所有记录
    print("\n--- 获取当前域名所有线路的 A 记录 ---")
    all_existing_records = get_all_existing_a_records()

    # 遍历所有定义的运营商线路
    for line_name, line_code in ISP_LINES.items():
        print(f"\n--- 正在处理线路: {line_name} ({line_code}) ---")

        # 1. 从已获取的全部记录中，筛选出属于当前线路的记录并删除
        records_to_delete = [record for record in all_existing_records if record.line == line_code]
        
        if records_to_delete:
            print(f"--- 发现 {len(records_to_delete)} 条属于线路 '{line_name}' 的旧记录，开始删除 ---")
            for record in records_to_delete:
                delete_dns_record(record.id)
        else:
            print(f"线路 '{line_name}' 没有需要删除的旧记录。")

        # 2. 在该线路下创建新的记录集
        print(f"--- 开始为线路 '{line_name}' 创建新的 DNS 记录 ---")
        create_dns_record_set(new_ips, line_code)
    
    print("\n--- 所有线路更新完成 ---")


if __name__ == '__main__':
    main()
