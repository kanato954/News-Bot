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

# 幅広いカテゴリのニュースフィード（国際・国内政治＋本日の主要出来事＋地方）
RSS_FEEDS = {
    "国際政治・外交": [
        "https://news.yahoo.co.jp/rss/topics/world.xml",
        "https://www.nhk.or.jp/rss/news/cat5.xml"
    ],
    "国内政治": [
        "https://news.yahoo.co.jp/rss/topics/domestic.xml",
        "https://www.nhk.or.jp/rss/news/cat4.xml"
    ],
    "本日の主な出来事": [
        "https://news.yahoo.co.jp/rss/topics/top-picks.xml",
        "https://www.nhk.or.jp/rss/news/cat0.xml",
        "https://news.yahoo.co.jp/rss/topics/business.xml"
    ],
    "地方・地域社会": [
        "https://news.yahoo.co.jp/rss/topics/local.xml",
        "https://www.47news.jp/rss/news.xml"
    ]
}

def get_x_realtime_posts(keyword):
    """Yahoo!リアルタイム検索からXの生ポストを取得（ユーザーエージェント偽装強化）"""
    posts = []
    try:
        encoded_kw = urllib.parse.quote(keyword)
        url = f"https://search.yahoo.co.jp/realtime/search?p={encoded_kw}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
        }
        req = urllib.request.Request(url, headers=headers)
        html = urllib.request.urlopen(req, timeout=5).read().decode("utf-8")
        soup = BeautifulSoup(html, "html.parser")
        
        # 投稿本文を抽出
        for tweet in soup.find_all(["p", "div"], class_=re.compile("TweetText|tweet|Tweet_body"))[:5]:
            text = tweet.get_text().strip()
            if text and len(text) > 10:
                posts.append(text.replace("\n", " "))
    except Exception as e:
        print(f"X (Realtime) fetch error for {keyword}: {e}")
    return posts

def fetch_all_news_with_raw_opinions():
    """多角的なニュース ＋ リアルタイムネット感情の抽出"""
    articles_data = []
    
    for category, urls in RSS_FEEDS.items():
        for url in urls:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:2]: # 各ソースから上位を取得
                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    summary = entry.get("summary", "")
                    
                    # 検索キーの作成（記号を取り除いた単語）
                    search_kw = re.sub(r"[【】（）( )\[\]]", " ", title).strip().split()[0] if title else ""
                    
                    x_posts = get_x_realtime_posts(search_kw) if search_kw else []
                    
                    articles_data.append({
                        "category": category,
                        "title": title,
                        "summary": summary,
                        "x_posts": x_posts
                    })
            except Exception as e:
                print(f"Error fetching RSS {url}: {e}")
                
    return articles_data

def summarize_with_gemini(articles_data):
    """Geminiを使って分析（生データ優先、足りない場合はAIの客観分析でカバー）"""
    client = genai.Client(api_key=GEMINI_KEY)
    
    formatted_input = []
    for idx, item in enumerate(articles_data, 1):
        formatted_input.append(f"""
--- トピック {idx} ---
カテゴリ: {item['category']}
タイトル: {item['title']}
概要: {item['summary']}
【抽出されたX（旧Twitter）リアルタイム投稿】:
{chr(10).join(['・' + p for p in item['x_posts']]) if item['x_posts'] else 'なし（自動推測が必要）'}
""")

    raw_text_block = "\n".join(formatted_input)
    
    prompt = f"""
あなたは徹底して中立・公平な報道アナリストです。
以下に提供される【国際政治、国内政治、本日の出来事、地方ニュース】のデータと【ネットのリアルタイム声データ】を分析してください。

【出力条件・指示】
1. **全体のバランス**: 国際政治、国内政治、本日の出来事から、特に重要なトピックを合計4〜5個選定してください。
2. **ネット世論（多数派意見）**: 
   - 抽出データがある場合はそれを反映し、データが少ない場合でも「この記事に対して一般的にネット・X上で多数派となりやすい主な意見・懸念の傾向」を記述してください。（「収集不可」「なし」と出力することは禁止です）
3. **中立視点**: ネットの感情的・偏向した見解に対し、感情論を排した客観的事実、歴史・制度的背景、対立する双方の主張（両論併記）を必ず添えてください。

【出力フォーマット】
以下の形式で出力してください。

TOPIC_START
タイトル: [絵文字] トピック名
カテゴリ: カテゴリ名（国際政治 / 国内政治 / 本日の出来事 / 地方社会）
概要: 事実関係の客観的な要約（100〜150文字程度）
ネット世論: Xやネットで見られる多数派の主な反応・懸念・意見
中立視点: 感情論を排除した中立的な事実背景・双方の視点
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
        
        # カテゴリごとにカードの枠線色を変更
        color = 3447003 # 青（デフォルト）
        if "国際" in category:
            color = 10181046 # 紫系（国際）
        elif "政治" in category:
            color = 15105570 # オレンジ/赤系（政治）
        elif "出来事" in category:
            color = 15844367 # 黄色系（主要出来事）
        elif "地方" in category:
            color = 3066993 # 緑系（地方）

        embed = {
            "title": title,
            "color": color,
            "fields": [
                {"name": "📰 事実概要", "value": summary if summary else "なし", "inline": False},
                {"name": "💬 ネット・Xの主な反応（多数派）", "value": net_opinion if net_opinion else "なし", "inline": False},
                {"name": "⚖️ 中立・客観的視点", "value": neutral_view if neutral_view else "なし", "inline": False}
            ],
            "footer": {"text": f"カテゴリ: {category} | 信頼メディア・リアルタイム感情分析"}
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
    print("ニュース・Xリアルタイム情報を取得中...")
    articles_data = fetch_all_news_with_raw_opinions()
    
    print("Geminiで分析中（国際・国内・出来事・ネット世論・中立視点）...")
    summary_result = summarize_with_gemini(articles_data)
    
    print("Discordへ送信中...")
    parse_and_send_to_discord(summary_result)
    print("完了しました。")

if __name__ == "__main__":
    main()
