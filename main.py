import os
import json
import urllib.request
import urllib.parse
import re
import feedparser
from bs4 import BeautifulSoup
from google import genai

# 環境変数の読み込み
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# 主要・地方・政治ニュースの信頼できるRSS
RSS_FEEDS = {
    "国内政治": [
        "https://news.yahoo.co.jp/rss/topics/domestic.xml",
        "https://www.nhk.or.jp/rss/news/cat4.xml"
    ],
    "国際政治": [
        "https://news.yahoo.co.jp/rss/topics/world.xml",
        "https://www.nhk.or.jp/rss/news/cat5.xml"
    ],
    "地方社会": [
        "https://news.yahoo.co.jp/rss/topics/local.xml",
        "https://www.47news.jp/rss/news.xml"
    ]
}

def get_x_realtime_posts(keyword):
    """Yahoo!リアルタイム検索からXの生のポストを取得"""
    posts = []
    try:
        encoded_kw = urllib.parse.quote(keyword)
        url = f"https://search.yahoo.co.jp/realtime/search?p={encoded_kw}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=5).read().decode("utf-8")
        soup = BeautifulSoup(html, "html.parser")
        
        # 投稿本文を抽出
        for tweet in soup.find_all("p", class_=re.compile("TweetText|tweet"))[:4]:
            text = tweet.get_text().strip()
            if text:
                posts.append(text.replace("\n", " "))
    except Exception as e:
        print(f"X (Realtime) fetch error for {keyword}: {e}")
    return posts

def get_yf_comments(news_url):
    """Yahoo!ニュースのコメント欄から高評価の生コメントを取得"""
    comments = []
    if "news.yahoo.co.jp" not in news_url:
        return comments
    try:
        comment_url = news_url.rstrip("/") + "/comments"
        req = urllib.request.Request(comment_url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=5).read().decode("utf-8")
        soup = BeautifulSoup(html, "html.parser")
        
        for c_box in soup.find_all("p", class_=re.compile("CommentText|comment"))[:3]:
            text = c_box.get_text().strip()
            if text:
                comments.append(text.replace("\n", " "))
    except Exception as e:
        print(f"Yf comment fetch error for {news_url}: {e}")
    return comments

def fetch_all_news_with_raw_opinions():
    """ニュース本文 + 生のX投稿 + 生のヤフコメを一括取得"""
    articles_data = []
    
    for category, urls in RSS_FEEDS.items():
        for url in urls:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:2]: # 各ソース上位2件（負荷抑制）
                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    summary = entry.get("summary", "")
                    
                    # 検索キー（タイトルから主要キーワード抽出）
                    search_kw = re.sub(r"[【】（）( )\[\]]", " ", title).split()[0] if title else ""
                    
                    # 生データ収集
                    x_posts = get_x_realtime_posts(search_kw) if search_kw else []
                    yf_comments = get_yf_comments(link)
                    
                    articles_data.append({
                        "category": category,
                        "title": title,
                        "summary": summary,
                        "x_posts": x_posts,
                        "yf_comments": yf_comments
                    })
            except Exception as e:
                print(f"Error fetching RSS {url}: {e}")
                
    return articles_data

def summarize_with_gemini(articles_data):
    """生のX投稿とヤフコメを統合分析し、中立的な視点を添えて要約を作成"""
    client = genai.Client(api_key=GEMINI_KEY)
    
    # AIに渡すテキストデータ構築
    formatted_input = []
    for idx, item in enumerate(articles_data[:4], 1): # 上位4トピックに厳選
        formatted_input.append(f"""
--- トピック {idx} ---
カテゴリ: {item['category']}
タイトル: {item['title']}
概要: {item['summary']}
【収集した生のX（旧Twitter）ポスト】:
{chr(10).join(['・' + p for p in item['x_posts']]) if item['x_posts'] else 'なし'}
【収集した生のYahoo!コメント】:
{chr(10).join(['・' + c for c in item['yf_comments']]) if item['yf_comments'] else 'なし'}
""")

    raw_text_block = "\n".join(formatted_input)
    
    prompt = f"""
あなたは客観的かつ厳格な政治・社会問題のアナリストです。
以下に提供される【ニュース記事】および【実際の生のX投稿データ】【生のヤフコメデータ】を分析してください。

【出力条件】
1. 主観や誇張を排除し、事実のみに基づいて分析すること。
2. 実際に収集されたX投稿およびヤフコメのテキストから、ネット上の「多数派の意見・感情の傾向」を要約してください。
3. 感情論や一方的なネット世論に対し、制度・歴史・対立意見などの「客観的・中立的な事実背景」を必ず添えてください（両論併記を徹底）。

【出力フォーマット】
以下の形式で出力してください。

TOPIC_START
タイトル: [絵文字] トピック名
カテゴリ: カテゴリ名
概要: 事実関係の簡単な要約（100文字程度）
ネット世論: Xの投稿やヤフコメから読み取れる多数派の主な反応・懸念
中立視点: ネット世論に対する冷静・客観的な事実と両論の視点
TOPIC_END

---
ニュースおよび収集データ:
{raw_text_block}
"""
    
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt
    )
    return response.text

def parse_and_send_to_discord(ai_response):
    """Discordの埋め込み（Embed）カード形式で送信"""
    blocks = ai_response.split("TOPIC_START")
    
    for block in blocks:
        if "TOPIC_END" not in block:
            continue
            
        content = block.split("TOPIC_END")[0].strip()
        lines = content.split("\n")
        
        title = "本日のニュース"
        category = "ニュース"
        summary = ""
        net_opinion = ""
        neutral_view = ""
        
        for line in lines:
            if line.startswith("タイトル:"):
                title = line.replace("タイトル:", "").strip()
            elif line.startswith("カテゴリ:"):
                category = line.replace("カテゴリ:", "").strip()
            elif line.startswith("概要:"):
                summary = line.replace("概要:", "").strip()
            elif line.startswith("ネット世論:"):
                net_opinion = line.replace("ネット世論:", "").strip()
            elif line.startswith("中立視点:"):
                neutral_view = line.replace("中立視点:", "").strip()
        
        # 枠線の色設定
        color = 3447003 # 青
        if "政治" in category:
            color = 15105570 # オレンジ
        elif "地方" in category:
            color = 3066993 # 緑

        embed = {
            "title": title,
            "color": color,
            "fields": [
                {"name": "📰 事実概要", "value": summary if summary else "なし", "inline": False},
                {"name": "💬 生のネット声（X・ヤフコメ多数派）", "value": net_opinion if net_opinion else "なし", "inline": False},
                {"name": "⚖️ 中立・客観的視点", "value": neutral_view if neutral_view else "なし", "inline": False}
            ],
            "footer": {"text": f"カテゴリ: {category} | Xリアルタイム＆ヤフコメ生データ取得"}
        }

        payload = {"embeds": [embed]}
        
        req = urllib.request.Request(
            DISCORD_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0"
            }
        )
        try:
            urllib.request.urlopen(req)
        except Exception as e:
            print(f"Error sending to Discord: {e}")

def main():
    print("ニュース・X生投稿・ヤフコメを取得中...")
    articles_data = fetch_all_news_with_raw_opinions()
    
    print("Geminiで分析中（生データから多数派意見＋中立視点を抽出）...")
    summary_result = summarize_with_gemini(articles_data)
    
    print("Discordへカード送信中...")
    parse_and_send_to_discord(summary_result)
    print("完了しました。")

if __name__ == "__main__":
    main()
