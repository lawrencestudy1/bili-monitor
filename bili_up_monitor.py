import requests
import time
import os
from datetime import datetime

# ============================================================
# 配置区域（Railway 环境变量注入，无需手动修改）
# UP_UID 支持多个UID，用英文逗号隔开，例如：123456,789012
# ============================================================
FEISHU_WEBHOOK = os.environ.get('FEISHU_WEBHOOK', '')
UP_UIDS_RAW    = os.environ.get('UP_UID', '')
UP_UIDS        = [uid.strip() for uid in UP_UIDS_RAW.split(',') if uid.strip()]
CHECK_INTERVAL = int(os.environ.get('CHECK_INTERVAL', '60'))
BILI_COOKIE    = os.environ.get('BILI_COOKIE', '')   # B站登录Cookie

seen_rpids = set()

def make_headers():
    """每次请求都带上最新的 Cookie"""
    h = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.bilibili.com/',
    }
    if BILI_COOKIE:
        h['Cookie'] = BILI_COOKIE
    return h

# -------------------------------------------------------
# 1. 获取某UP主的最新视频列表
# -------------------------------------------------------
def get_latest_videos(uid: str, count: int = 10) -> list[tuple[str, str]]:
    url = (
        f"https://api.bilibili.com/x/space/arc/search"
        f"?mid={uid}&ps={count}&pn=1&order=pubdate&jsonp=jsonp"
    )
    try:
        resp = requests.get(url, headers=make_headers(), timeout=15)
        data = resp.json()
        vlist = data.get('data', {}).get('list', {}).get('vlist', [])
        return [(str(v['aid']), v['title']) for v in vlist]
    except Exception as e:
        print(f"[ERROR] 获取视频列表失败 uid={uid}: {e}")
        return []

# -------------------------------------------------------
# 2. 获取某视频下 UP 主本人的评论和二级回复
# -------------------------------------------------------
def get_up_top_comments(aid: str, up_uid: str, video_title: str, up_name: str) -> list[dict]:
    results = []
    for pn in range(1, 4):
        url = (
            f"https://api.bilibili.com/x/v2/reply"
            f"?type=1&oid={aid}&sort=0&pn={pn}&ps=20"
        )
        try:
            resp = requests.get(url, headers=make_headers(), timeout=15)
            data = resp.json()
            replies = data.get('data', {}).get('replies') or []
            if not replies:
                break
        except Exception as e:
            print(f"[ERROR] 获取评论失败 aid={aid}: {e}")
            break

        for r in replies:
            rpid = r['rpid']
            mid  = str(r['member']['mid'])

            if mid == up_uid and rpid not in seen_rpids:
                seen_rpids.add(rpid)
                results.append({
                    'type':        '📝 UP主发表评论',
                    'up_name':     up_name,
                    'video_title': video_title,
                    'video_url':   f"https://www.bilibili.com/video/av{aid}",
                    'content':     r['content']['message'],
                    'time':        datetime.fromtimestamp(r['ctime']).strftime('%Y-%m-%d %H:%M:%S'),
                    'reply_url':   f"https://www.bilibili.com/video/av{aid}#reply{rpid}",
                })

            sub_replies = r.get('replies') or []
            for sr in sub_replies:
                sr_rpid = sr['rpid']
                sr_mid  = str(sr['member']['mid'])
                if sr_mid == up_uid and sr_rpid not in seen_rpids:
                    seen_rpids.add(sr_rpid)
                    reply_to = sr['content'].get('members', [{}])[0].get('uname', '某位用户') \
                               if sr['content'].get('members') else r['member']['uname']
                    results.append({
                        'type':        '💬 UP主回复了评论',
                        'up_name':     up_name,
                        'video_title': video_title,
                        'video_url':   f"https://www.bilibili.com/video/av{aid}",
                        'reply_to':    reply_to,
                        'content':     sr['content']['message'],
                        'time':        datetime.fromtimestamp(sr['ctime']).strftime('%Y-%m-%d %H:%M:%S'),
                        'reply_url':   f"https://www.bilibili.com/video/av{aid}#reply{sr_rpid}",
                    })

        time.sleep(0.3)

    return results

# -------------------------------------------------------
# 3. 获取UP主昵称
# -------------------------------------------------------
def get_up_name(uid: str) -> str:
    url = f"https://api.bilibili.com/x/space/wbi/acc/info?mid={uid}"
    try:
        resp = requests.get(url, headers=make_headers(), timeout=10)
        data = resp.json()
        return data.get('data', {}).get('name', f'UID:{uid}')
    except:
        return f'UID:{uid}'

# -------------------------------------------------------
# 4. 推送到飞书
# -------------------------------------------------------
def send_to_feishu(item: dict):
    if item.get('reply_to'):
        text = (
            f"{item['type']} · {item['up_name']}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📺 视频：{item['video_title']}\n"
            f"↩️  回复对象：@{item['reply_to']}\n"
            f"💬 内容：{item['content']}\n"
            f"🕐 时间：{item['time']}\n"
            f"🔗 {item['reply_url']}"
        )
    else:
        text = (
            f"{item['type']} · {item['up_name']}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📺 视频：{item['video_title']}\n"
            f"💬 内容：{item['content']}\n"
            f"🕐 时间：{item['time']}\n"
            f"🔗 {item['reply_url']}"
        )
    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        print(f"[飞书] ✓ 已推送 [{item['up_name']}]: {item['content'][:30]}...")
    except Exception as e:
        print(f"[ERROR] 飞书推送失败: {e}")

# -------------------------------------------------------
# 主循环
# -------------------------------------------------------
def main():
    if not FEISHU_WEBHOOK or not UP_UIDS:
        print("[ERROR] 请配置 FEISHU_WEBHOOK 和 UP_UID 环境变量！")
        return

    if not BILI_COOKIE:
        print("[WARNING] 未配置 BILI_COOKIE，B站可能拒绝返回评论数据！")
    else:
        print("[INFO] ✓ 已加载 B站 Cookie，登录态请求已启用")

    up_names = {}
    for uid in UP_UIDS:
        up_names[uid] = get_up_name(uid)

    print(f"🚀 UP主动态监控启动")
    print(f"   监控数量    : {len(UP_UIDS)} 位UP主")
    for uid in UP_UIDS:
        print(f"   · {up_names[uid]} (UID: {uid})")
    print(f"   检查间隔    : {CHECK_INTERVAL} 秒")
    print(f"   监控范围    : UP主本人的评论 & 回复（含二级回复）")

    print("\n⏳ 初始化中，正在记录已有评论，请稍候...")
    for uid in UP_UIDS:
        videos = get_latest_videos(uid)
        for aid, title in videos:
            get_up_top_comments(aid, uid, title, up_names[uid])
    print(f"✅ 初始化完成，共记录 {len(seen_rpids)} 条已有内容，开始实时监控！\n")

    while True:
        time.sleep(CHECK_INTERVAL)
        ts = datetime.now().strftime('%H:%M:%S')
        print(f"[{ts}] 检查中（共监控 {len(UP_UIDS)} 位UP主）...")

        new_items = []
        for uid in UP_UIDS:
            videos = get_latest_videos(uid)
            for aid, title in videos:
                new_items.extend(get_up_top_comments(aid, uid, title, up_names[uid]))

        if new_items:
            print(f"🔔 发现 {len(new_items)} 条新动态！")
            for item in new_items:
                send_to_feishu(item)
                time.sleep(0.5)
        else:
            print("   暂无新动态。")

if __name__ == '__main__':
    main()
