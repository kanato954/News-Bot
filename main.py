import os
import json
import urllib.request
import feedparser
from google import genai

# 環境変数の読み込み
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# 国内外の主要RSSフィード
RSS_FEEDS = {
    "日本（国内・経済）": [
        "https://news.yahoo.co.jp/rss/topics/top-picks.xml",
        "https://news.yahoo.co.jp/rss/topics/business.xml",
        "https://news.yahoo.co.jp/rss/topics/world.xml"
    ],
    "海外（国際・ビジネス）": [
        "http://feeds.bbci.co.uk/news/world/rss.xml",
        "http://feeds.bbci.co.uk/news/business/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"
    ]
}

def fetch_all_news():
    """国内外のフィードから大量のニューステキストを抽出"""
    all_articles = []
    
    for category, urls in RSS_FEEDS.items():
        all_articles.append(f"=== {category} ===")
        for url in urls:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")
                    all_articles.append(f"・件名: {title}\n  詳細: {summary}")
            except Exception as e:
                print(f"Error fetching {url}: {e}")
                
    return "\n".join(all_articles)

def summarize_with_gemini(raw_news_text):
    """Gemini 2.5 Flashを使って厳格に中立的な要約を作成"""
    client = genai.Client(api_key=GEMINI_KEY)
    
    prompt = f"""
あなたは徹底して中立・公平なジャーナリスト兼アナリストです。
以下に提示する大量の国内外ニュースを読み込み、特定の発言者や政治的立場、偏向した論説を排除した上で「客観的な事実（Fact）」のみを抽出・整理してください。

【出力要件】
1. 主観や誇張表現を排除し、事実関係のみを客観的なトーンで記述すること。
2. 対立する意見や国ごとの主張が分かれるトピックについては、一方のみを正当化せず「A側は〜と主張、B側は〜と反論」のように両論併記で記述すること。
3. 全体としての大きな潮流（国内・国際）を箇条書きでまとめてください。

【出力フォーマット】
🌍 **【本日の国際・国内ニュース中立要約】**

■ **主要トピックと客観的背景**（3〜4項目）
・[項目名]: 事実の要約

■ **本日の主な潮流・課題**
・全体の動向まとめ

---
ニュースデータ:
{raw_news_text}
"""
    
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=prompt
    )
    return response.text

def send_to_discord(content):
    """Discordの文字数制限（2000文字）に配慮して送信"""
    chunks = [content[i:i+1900] for i in range(0, len(content), 1900)]
    
    for chunk in chunks:
        payload = {"content": chunk}
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
    print("ニュースを取得中...")
    raw_text = fetch_all_news()
    
    print("Geminiで中立要約を作成中...")
    summary_result = summarize_with_gemini(raw_text)
    
    print("Discordへ送信中...")
    send_to_discord(summary_result)
    print("完了しました。")

if __name__ == "__main__":
    main()
