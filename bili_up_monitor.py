import requests
import time
import json
import os
from datetime import datetime

# ============================================================
# 配置区域（Railway 环境变量注入，无需手动修改）
# ============================================================
FEISHU_WEBHOOK = os.environ.get('FEISHU_WEBHOOK', '')
UP_UID         = os.environ.get('UP_UID', '')           # UP主的数字 UID
CHECK_INTERVAL = int(os.environ.get('CHECK_INTERVAL', '60'))  # 默认60秒

seen_rpids = set()  # 已推送过的评论/回复 ID

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://www.bilibili.com/',
}

# -------------------------------------------------------
# 1. 获取 UP 主最新视频列表
# -------------------------------------------------------
def get_latest_videos(uid: str, count: int = 10) -> list[tuple[str, str]]:
    url = (
        f"https://api.bilibili.com/x/space/arc/search"
        f"?mid={uid}&ps={count}&pn=1&order=pubdate&jsonp=jsonp"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        data = resp.json()
        vlist = data.get('data', {}).get('list', {}).get('vlist', [])
        return [(str(v['aid']), v['title']) for v in vlist]
    except Exception as e:
        print(f"[ERROR] 获取视频列表失败: {e}")
        return []

# -------------------------------------------------------
# 2. 获取某视频下所有顶层评论，筛选 UP 主本人发的
# -------------------------------------------------------
def get_up_top_comments(aid: str, up_uid: str, video_title: str) -> list[dict]:
    results = []
    # 抓前3页，基本覆盖UP主置顶/精选评论区
    for pn in range(1, 4):
        url = (
            f"https://api.bilibili.com/x/v2/reply"
            f"?type=1&oid={aid}&sort=0&pn={pn}&ps=20"
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            data = resp.json()
            replies = data.get('data', {}).get('replies') or []
            if not replies:
                break
        except Exception as e:
            print(f"[ERROR] 获取顶层评论失败 aid={aid} pn={pn}: {e}")
            break

        for r in replies:
            rpid = r['rpid']
            mid  = str(r['member']['mid'])

            # ── 顶层评论：是否为 UP 主本人 ──
            if mid == up_uid and rpid not in seen_rpids:
                seen_rpids.add(rpid)
                results.append({
                    'type':        '📝 UP主发表评论',
                    'video_title': video_title,
                    'video_url':   f"https://www.bilibili.com/video/av{aid}",
                    'content':     r['content']['message'],
                    'time':        datetime.fromtimestamp(r['ctime']).strftime('%Y-%m-%d %H:%M:%S'),
                    'reply_url':   f"https://www.bilibili.com/video/av{aid}#reply{rpid}",
                })

            # ── 二级回复：遍历该楼层下的所有回复，找 UP 主的回复 ──
            sub_replies = r.get('replies') or []
            for sr in sub_replies:
                sr_rpid = sr['rpid']
                sr_mid  = str(sr['member']['mid'])
                if sr_mid == up_uid and sr_rpid not in seen_rpids:
                    seen_rpids.add(sr_rpid)
                    # 被回复的对象
                    reply_to = sr['content'].get('members', [{}])[0].get('uname', '某位用户') \
                               if sr['content'].get('members') else r['member']['uname']
                    results.append({
                        'type':        '💬 UP主回复了评论',
                        'video_title': video_title,
                        'video_url':   f"https://www.bilibili.com/video/av{aid}",
                        'reply_to':    reply_to,
                        'content':     sr['content']['message'],
                        'time':        datetime.fromtimestamp(sr['ctime']).strftime('%Y-%m-%d %H:%M:%S'),
                        'reply_url':   f"https://www.bilibili.com/video/av{aid}#reply{sr_rpid}",
                    })

        time.sleep(0.3)  # 礼貌性间隔，避免触发限速

    return results

# -------------------------------------------------------
# 3. 针对有大量回复的热门楼层，单独抓取完整二级回复
#    （API 默认只返回前3条二级回复，热门楼层需额外请求）
# -------------------------------------------------------
def get_up_sub_replies(aid: str, up_uid: str, root_rpid: str,
                       video_title: str) -> list[dict]:
    results = []
    url = (
        f"https://api.bilibili.com/x/v2/reply/reply"
        f"?type=1&oid={aid}&root={root_rpid}&ps=20&pn=1"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        data = resp.json()
        replies = data.get('data', {}).get('replies') or []
    except Exception as e:
        print(f"[ERROR] 获取二级回复失败: {e}")
        return []

    for sr in replies:
        sr_rpid = sr['rpid']
        sr_mid  = str(sr['member']['mid'])
        if sr_mid == up_uid and sr_rpid not in seen_rpids:
            seen_rpids.add(sr_rpid)
            reply_to = sr['content'].get('members', [{}])[0].get('uname', '某位用户') \
                       if sr['content'].get('members') else '某位用户'
            results.append({
                'type':        '💬 UP主回复了评论',
                'video_title': video_title,
                'video_url':   f"https://www.bilibili.com/video/av{aid}",
                'reply_to':    reply_to,
                'content':     sr['content']['message'],
                'time':        datetime.fromtimestamp(sr['ctime']).strftime('%Y-%m-%d %H:%M:%S'),
                'reply_url':   f"https://www.bilibili.com/video/av{aid}#reply{sr_rpid}",
            })
    return results

# -------------------------------------------------------
# 4. 推送到飞书
# -------------------------------------------------------
def send_to_feishu(item: dict):
    if item.get('reply_to'):
        text = (
            f"{item['type']}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📺 视频：{item['video_title']}\n"
            f"↩️  回复对象：@{item['reply_to']}\n"
            f"💬 内容：{item['content']}\n"
            f"🕐 时间：{item['time']}\n"
            f"🔗 {item['reply_url']}"
        )
    else:
        text = (
            f"{item['type']}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📺 视频：{item['video_title']}\n"
            f"💬 内容：{item['content']}\n"
            f"🕐 时间：{item['time']}\n"
            f"🔗 {item['reply_url']}"
        )

    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        print(f"[飞书] ✓ 已推送: {item['content'][:30]}...")
    except Exception as e:
        print(f"[ERROR] 飞书推送失败: {e}")

# -------------------------------------------------------
# 主循环
# -------------------------------------------------------
def main():
    if not FEISHU_WEBHOOK or not UP_UID:
        print("[ERROR] 请配置 FEISHU_WEBHOOK 和 UP_UID 环境变量！")
        return

    print(f"🚀 UP主动态监控启动")
    print(f"   监控对象 UID : {UP_UID}")
    print(f"   检查间隔    : {CHECK_INTERVAL} 秒")
    print(f"   监控范围    : UP主本人的评论 & 回复（含二级回复）")

    # 初始化：静默记录已有内容，避免启动后刷屏
    print("\n⏳ 初始化中，正在记录已有评论，请稍候...")
    videos = get_latest_videos(UP_UID)
    for aid, title in videos:
        get_up_top_comments(aid, UP_UID, title)
    print(f"✅ 初始化完成，共记录 {len(seen_rpids)} 条已有内容，开始实时监控！\n")

    while True:
        time.sleep(CHECK_INTERVAL)
        ts = datetime.now().strftime('%H:%M:%S')
        print(f"[{ts}] 检查中...")

        videos = get_latest_videos(UP_UID)
        new_items: list[dict] = []

        for aid, title in videos:
            new_items.extend(get_up_top_comments(aid, UP_UID, title))

        if new_items:
            print(f"🔔 发现 {len(new_items)} 条UP主新动态！")
            for item in new_items:
                send_to_feishu(item)
                time.sleep(0.5)
        else:
            print("   UP主暂无新动态。")

if __name__ == '__main__':
    main()
