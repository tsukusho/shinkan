import os
import logging
import functions_framework
import json
import time
import traceback
from urllib.parse import quote_plus, unquote, urlparse
import requests
from bs4 import BeautifulSoup
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime
from flask import jsonify

# ロギング設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 環境変数
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")
PROJECT_ID = os.environ.get("PROJECT_ID", "magiclpo-dev")
LOCATION = os.environ.get("LOCATION", "us-central1")
GOOGLE_SHEETS_CREDENTIALS = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")

# グローバル変数の初期化
gemini_model = None
slack_client = None
selenium_initialized = False
sheets_service = None

# 過去に処理したイベントIDを保存するセット（インメモリキャッシュ）
processed_event_ids = set()
# キャッシュの有効期限（秒）
EVENT_CACHE_TTL = 60 * 5  # 5分
# イベントIDとタイムスタンプのマッピング
event_timestamps = {}

# 検索データ保存用の一時ファイルパス
SEARCH_DATA_FILE = "/tmp/search_data.json"
# LP分析データ保存用の一時ファイルパス
LP_ANALYSIS_FILE = "/tmp/lp_analysis.json"
# インプレッションシェアデータ保存用の一時変数
impression_share_data = None

def init_services():
    """SlackクライアントとVertexAIを初期化する"""
    global slack_client, gemini_model
    
    try:
        if slack_client is None:
            from slack_sdk import WebClient
            slack_client = WebClient(
                token=SLACK_BOT_TOKEN,
                base_url="https://slack.com/api/" 
            )
            logger.info("Slackクライアント初期化完了")
    except Exception as e:
        logger.error(f"Slack初期化エラー: {str(e)}")
        traceback.print_exc()

def send_slack_message(channel, text, thread_ts=None):
    """Slackにメッセージを送信する"""
    global slack_client
    
    if slack_client is None:
        init_services()
        
    try:
        response = slack_client.chat_postMessage(
            channel=channel,
            text=text,
            thread_ts=thread_ts
        )
        logger.info(f"Slackメッセージ送信: {text[:30]}...")
        return response
    except Exception as e:
        logger.error(f"Slackメッセージ送信エラー: {str(e)}")
        traceback.print_exc()
        return None

def generate_ai_response(prompt, history=None):
    """Geminiモデルを使ってAI応答を生成する"""
    global gemini_model
    
    try:
        # VertexAIが初期化されていない場合は初期化
        if gemini_model is None:
            init_vertexai()
        
        if history:
            # 履歴を含めた生成
            response = gemini_model.generate_content([prompt] + history)
        else:
            # 単一プロンプトでの生成
            response = gemini_model.generate_content(prompt)
        
        # 応答テキストを返す
        response_text = response.text
        logger.info(f"AI応答生成: {response_text[:500]}...")
        return response_text
    except Exception as e:
        logger.error(f"AI応答生成エラー: {str(e)}")
        traceback.print_exc()
        raise e

def generate_keywords(query, is_second_phase=False, previous_report=None):
    """ユーザーの質問から検索に使えるキーワードを生成する"""
    try:
        # VertexAIが初期化されていない場合は初期化
        if gemini_model is None:
            init_vertexai()
        
        if is_second_phase and previous_report:
            # 第2フェーズのキーワード生成（より具体的な情報を得るため）
            prompt = f"""
あなたはSEOに詳しい検索エキスパートです。
以下の質問と第1フェーズで生成したレポートから、より詳細で具体的な情報を得るための検索クエリを8個生成してください。

元の質問: {query}

第1フェーズのレポート:
{previous_report}

【質問意図の分析】
まず、ユーザーの質問の本質的な意図を分析してください。
例えば「AIエージェントの軌跡と今後の進展について知りたい」という質問であれば:
- 主要軸1: AIエージェントの歴史的発展（過去から現在までの重要な出来事や転換点）
- 主要軸2: AIエージェントの今後の進展予測（将来の発展方向性、企業の取り組み、予測される影響）
このように質問の核心となる1〜2個の主要軸を特定し、それに沿ったキーワードを生成してください。
第2フェーズでは、特に「質問の主要軸」を中心に、質問者の立場に立ってください。そして第一フェーズのレポートを読んだときに、この部分についてもっと知りたい
と思うような、元の質問者がより知りたい、ここってどうなんだろう？と疑問を感じる部分がどこの部分かに焦点を当て、その部分についてより詳細で具体的な情報を得るための検索クエリを8個生成してください。
第一フェーズのレポートをそのまま出力すると、この質問者はどのような追加の質問を投げかけてくるのだろう？というところに焦点を置くようにしなさい。

以下の形式のJSONで出力してください。JSONの前後に余計な説明は不要です：
["検索クエリ1", "検索クエリ2", ...]
"""
        else:
            # 第1フェーズのキーワード生成
            prompt = f"""
あなたはSEOに詳しい検索エキスパートです。
以下の質問に関連するシンプルで効果的な検索クエリを6個生成してください。

質問: {query}

検索クエリを生成する際の重要なポイント：
1. シンプルかつ直接的なキーワードの組み合わせを使用する（最大で3-4語程度）
2. 質問の主要な要素や軸を必ず含める
3. 日本語のみを使用する（英語キーワードは不要）
4. 複雑な修飾語を避け、検索エンジンが理解しやすい基本的な単語を選ぶ
5. 質問の異なる側面をカバーする多様なクエリを作成する

例えば「AIの将来性と課題について知りたい」という質問であれば：
["AI 将来性", "AI 課題", "AI 発展 予測", "AI 問題点", "AI メリット デメリット", "AI 技術革新"]
のようなシンプルな組み合わせが効果的です。

以下の形式のJSONで出力してください。JSONの前後に余計な説明は不要です：
["検索クエリ1", "検索クエリ2", ...]
"""
        
        response = gemini_model.generate_content(prompt)
        output = response.text
        logger.info(f"キーワード生成: {output[:100]}...")
        
        # 余分な文字列を取り除いてJSONのみを抽出
        json_match = re.search(r'\[.*?\]', output, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            try:
                # JSONの検証
                keywords = json.loads(json_str)
                if is_second_phase:
                    return keywords[:8]  # 最大8個のキーワードに制限（第2フェーズ）
                else:
                    return keywords[:6]  # 最大6個のキーワードに制限（第1フェーズ）
            except json.JSONDecodeError:
                logger.error(f"生成されたJSONの形式が不正: {json_str}")
                # 代替手段として文字列を分割
                return [query]
        # JSONが見つからない場合は質問自体を返す
        logger.warning("キーワードのJSONが見つかりません。質問をそのまま使用します。")
        return [query]  # 修正：変数名を question から query に変更
    except Exception as e:
        logger.error(f"キーワード生成エラー: {str(e)}")
        traceback.print_exc()
        return [query]  # 修正：変数名を question から query に変更

def save_search_data(query, data, mode="w"):
    """検索データをJSONファイルに保存する"""
    try:
        # ファイルが存在し、モードが追記の場合
        if os.path.exists(SEARCH_DATA_FILE) and mode == "a":
            # 既存のデータを読み込む
            with open(SEARCH_DATA_FILE, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
            
            # 新しいデータをマージ
            if "keywords" in existing_data:
                existing_data["keywords"].append({
                    "keyword": query,
                    "results": data.get("results", [])
                })
            else:
                existing_data["keywords"] = [{
                    "keyword": query,
                    "results": data.get("results", [])
                }]
            
            # 更新データを書き込む
            with open(SEARCH_DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)
            
            # ここでのログ出力は削除（最後にまとめて出力するため）
        else:
            # 新規作成または上書き
            search_data = {
                "query_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "original_query": query,
                "keywords": [{
                    "keyword": query,
                    "results": data.get("results", [])
                }]
            }
            
            with open(SEARCH_DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(search_data, f, ensure_ascii=False, indent=2)
            
            # ここでのログ出力は削除（最後にまとめて出力するため）
        
        logger.info(f"検索データをJSONに保存しました: {SEARCH_DATA_FILE}")
        return True
    except Exception as e:
        logger.error(f"検索データ保存エラー: {str(e)}")
        traceback.print_exc()
        return False

def handle_slack_event(event_data):
    """Slackイベントを処理する"""
    global slack_client, processed_event_ids, event_timestamps, impression_share_data
    
    # サービスが初期化されていない場合は初期化
    if slack_client is None:
        init_services()
    
    # イベントがない場合は何もしない
    if "event" not in event_data:
        return {"status": "No event found"}
    
    event = event_data["event"]
    event_type = event.get("type")
    event_id = event_data.get("event_id")
    event_time = event_data.get("event_time")
    
    # キャッシュのクリーンアップ（古いイベントIDを削除）
    current_time = time.time()
    expired_events = [eid for eid, ts in event_timestamps.items() 
                     if current_time - ts > EVENT_CACHE_TTL]
    for eid in expired_events:
        if eid in processed_event_ids:
            processed_event_ids.remove(eid)
        if eid in event_timestamps:
            del event_timestamps[eid]
    
    # 重複イベントチェック
    if event_id and event_id in processed_event_ids:
        logger.info(f"重複イベントをスキップ: {event_id}")
        return {"status": "Duplicate event skipped"}
        
    # イベントIDをキャッシュに追加
    if event_id:
        processed_event_ids.add(event_id)
        event_timestamps[event_id] = time.time()
    
    try:
        if event_type in ["app_mention", "message"]:
            channel = event.get("channel")
            text = event.get("text", "").strip()
            user = event.get("user")
            thread_ts = event.get("thread_ts") or event.get("ts")
            
            # ボットのユーザーIDを取得 (app_mentionの場合のみ)
            bot_user_id = None
            if event_type == "app_mention" and "<@" in text:
                bot_user_id = text.split()[0].strip("<@>")
                # ボットのメンションを取り除いてクエリを抽出
                text = text.replace(f"<@{bot_user_id}>", "").strip()
                
                # メンションを受けた直後に初期メッセージを送信
                send_slack_message(channel, "分析を開始しています...", thread_ts)
            
            # /dataオプションの判定
            is_data_option = "/data" in text
            
            # URLを抽出
            url_pattern = r'https?://[^\s<>]+'
            url_match = re.search(url_pattern, text)
            target_url = None
            if url_match:
                target_url = url_match.group(0)
                
                # URLの前処理
                target_url = target_url.strip()
                target_url = re.sub(r'[<>"]$', '', target_url)
                
                # httpがない場合は追加
                if not target_url.startswith(('http://', 'https://')):
                    target_url = 'https://' + target_url
            
            # CSVデータかインプレッションシェアデータを含むかチェック
            has_csv_data = "表示 URL ドメイン" in text and "インプレッション シェア" in text
            
            # CSVデータの柔軟な検出
            if not has_csv_data and is_data_option:
                # コンマで区切られたデータがあるか確認
                csv_pattern = r'([a-zA-Z0-9_\-\.]+\.[a-zA-Z]{2,})\s*,\s*(\d+\.?\d*%?)'
                csv_matches = re.findall(csv_pattern, text)
                if csv_matches and len(csv_matches) >= 2:  # 少なくとも2つのドメインとシェアのペアがあれば
                    has_csv_data = True
                    logger.info(f"柔軟なCSVデータ形式を検出: {csv_matches}")
            
            try:
                # /dataオプションの場合はCSVデータの処理を優先
                if is_data_option and has_csv_data:
                    # CSVの内容をAIに分析させる
                    send_slack_message(channel, "CSVデータを分析しています...", thread_ts)
                    csv_analysis_result = analyze_csv_data(text)
                    
                    # CSVからインプレッションシェアの高い順にドメインを取得
                    try:
                        # JSON文字列をパース
                        domain_share_list = json.loads(csv_analysis_result)
                        
                        # エラーチェック
                        if len(domain_share_list) == 1 and domain_share_list[0].startswith("エラー:"):
                            send_slack_message(channel, "CSVデータの解析に失敗しました。正しいフォーマットでCSVデータを提供してください。", thread_ts)
                            return {"status": "error", "message": "CSVデータの解析に失敗"}
                        
                        # ドメインとシェアを抽出 (形式: "domain.com: 53.92%")
                        top_domains = []
                        for item in domain_share_list:
                            parts = item.split(": ")
                            if len(parts) == 2:
                                domain = parts[0].strip()
                                share = parts[1].strip().replace("%", "")
                                top_domains.append((domain, share))
                        
                        if not top_domains:
                            send_slack_message(channel, "CSVデータからドメインとシェアを抽出できませんでした。", thread_ts)
                            return {"status": "error", "message": "ドメイン抽出に失敗"}
                    except Exception as e:
                        logger.error(f"JSON解析エラー: {str(e)}")
                        send_slack_message(channel, f"データ解析中にエラーが発生しました: {str(e)}", thread_ts)
                        return {"status": "error", "message": f"JSON解析エラー: {str(e)}"}
                    
                    # インプレッションシェアデータを構築
                    impression_data = {
                        'competitors': [
                            {
                                '表示 URL ドメイン': domain,
                                'インプレッション シェア': f"{share}%",
                                'impression_share_value': float(share.replace(',', '.'))
                            } for domain, share in top_domains
                        ]
                    }
                    
                    # 元のLP情報用の変数を事前に定義（分岐によらず使用するため）
                    original_url = target_url if target_url else "https://example.com"
                    
                    # ダミー分析結果を事前に作成（target_urlがない場合に使用）
                    dummy_analysis = {
                        "title": "インプレッションシェアデータ分析",
                        "description": "インプレッションシェアに基づく競合分析",
                        "url": original_url,
                        "main_content": json.dumps(domain_share_list),
                        "meta_tags": {},
                        "headers": [],
                        "images": [],
                        "analysis": f"インプレッションシェアの高い順に競合を分析:\n{csv_analysis_result}"
                    }
                    
                    # CSVから取得したドメインをキーワードとして使用
                    additional_keywords = [domain for domain, _ in top_domains if domain != "自分"]
                    
                    # ここから共通処理
                    # CSVデータ＋自社URLの場合（target_urlがある場合）
                    if target_url:
                        try:
                            # 自社LP分析
                            send_slack_message(channel, "自社LPを分析しています...", thread_ts)
                            original_analysis = analyze_landing_page(target_url)
                            logger.info(f"自社LP分析完了: {target_url}")
                            
                            logger.info(f"CSVデータから追加のキーワード: {additional_keywords}")
                            
                            # 類似LP検索（CSVから抽出したドメインを追加キーワードとして使用）
                            send_slack_message(channel, "類似のLPを検索しています...", thread_ts)
                            similar_analyses_data = find_similar_landing_pages(
                                target_url, 
                                original_analysis, 
                                impression_data,
                                additional_keywords=additional_keywords
                            )
                            
                            # レポート生成
                            send_slack_message(channel, "分析レポートを生成しています...", thread_ts)
                            full_report = generate_lp_analysis_report(target_url, original_analysis, similar_analyses_data)
                            
                        except Exception as e:
                            logger.error(f"自社LP分析エラー: {str(e)}")
                            send_slack_message(channel, f"自社LPの分析中にエラーが発生しました: {str(e)}。競合分析のみ実行します。", thread_ts)
                            # エラーの場合はダミー分析を使用
                            similar_analyses_data = find_similar_landing_pages(
                                original_url,
                                dummy_analysis, 
                                impression_data,
                                additional_keywords=additional_keywords
                            )
                            full_report = generate_lp_analysis_report(original_url, dummy_analysis, similar_analyses_data)
                    
                    # CSVデータのみの場合（target_urlがない場合）
                    else:
                        logger.info(f"インプレッションシェアデータを使用して競合を選択: {len(additional_keywords)}件")
                        
                        # DuckDuckGoでドメインを調べてLP分析（dummy_analysisを使用）
                        send_slack_message(channel, "インプレッションシェアデータを基に競合LPを分析しています...", thread_ts)
                        
                        # 類似LP検索（CSVから抽出したドメインをキーワードとして使用）
                        similar_analyses_data = find_similar_landing_pages(
                            original_url, 
                            dummy_analysis, 
                            impression_data,
                            additional_keywords=additional_keywords
                        )
                        
                        # レポート生成
                        send_slack_message(channel, "分析レポートを生成しています...", thread_ts)
                        full_report = generate_lp_analysis_report(original_url, dummy_analysis, similar_analyses_data)
                    
                    # スプレッドシートのURLを抽出して送信（共通処理）
                    try:
                        spreadsheet_url_match = re.search(r'https://docs\.google\.com/spreadsheets/[^\s]+', full_report)
                        if spreadsheet_url_match:
                            spreadsheet_url = spreadsheet_url_match.group(0)
                            # Slackに完了メッセージとスプレッドシートのURLのみを送信
                            send_slack_message(channel, f"<@{user}> LP分析が完了しました！\n分析結果はこちらのスプレッドシートをご確認ください：\n{spreadsheet_url}", thread_ts)
                        else:
                            # URLが見つからない場合は完了メッセージのみ
                            send_slack_message(channel, f"<@{user}> LP分析が完了しました！", thread_ts)
                        
                        return {"status": "success", "message": "LP分析完了"}
                    except Exception as e:
                        error_message = f"レポート生成エラー: {str(e)}\n{traceback.format_exc()}"
                        logger.error(error_message)
                        send_slack_message(channel, f"レポート生成中にエラーが発生しました: {str(e)}", thread_ts)
                        return {"status": "error", "message": str(e)}
                
                # 従来のインプレッションシェアデータ処理
                elif has_csv_data and not is_data_option:
                    # インプレッションシェアデータを解析
                    global impression_share_data
                    impression_share_data = parse_impression_share_data(text)
                    
                    if impression_share_data:
                        send_slack_message(channel, "インプレッションシェアデータを解析しました。URLの分析を開始します...", thread_ts)
                    else:
                        send_slack_message(channel, "インプレッションシェアデータの解析に失敗しました。", thread_ts)
                        return {"status": "error", "message": "インプレッションシェアデータの解析に失敗"}
                
                # URLが指定されている場合はLP分析
                if url_match and not is_data_option:
                    # URLの有効性を確認
                    url_parts = requests.utils.urlparse(target_url)
                    if not all([url_parts.scheme, url_parts.netloc]):
                        raise ValueError("無効なURLです")
                    
                    # LP分析の実行
                    analysis_result = analyze_landing_page(target_url)
                    if analysis_result:
                        # 類似LP検索
                        send_slack_message(channel, "類似のLPを検索しています...", thread_ts)
                        similar_analyses_data = find_similar_landing_pages(target_url, analysis_result, impression_share_data)
                        
                        # レポート生成
                        send_slack_message(channel, "分析レポートを生成しています...", thread_ts)
                        full_report = generate_lp_analysis_report(target_url, analysis_result, similar_analyses_data)
                        
                        # スプレッドシートのURLを抽出
                        spreadsheet_url_match = re.search(r'https://docs\.google\.com/spreadsheets/[^\s]+', full_report)
                        if spreadsheet_url_match:
                            spreadsheet_url = spreadsheet_url_match.group(0)
                            # Slackに完了メッセージとスプレッドシートのURLのみを送信
                            send_slack_message(channel, f"<@{user}> LP分析が完了しました！\n分析結果はこちらのスプレッドシートをご確認ください：\n{spreadsheet_url}", thread_ts)
                        else:
                            # URLが見つからない場合は完了メッセージのみ
                            send_slack_message(channel, "LP分析が完了しました！", thread_ts)
                        
                        return {"status": "success", "message": "LP分析完了"}
                # URLもCSVデータもない場合はヘルプを表示
                elif not (is_data_option and has_csv_data):
                    if is_data_option:
                        help_message = """
'/data'オプションを使用する場合は、CSVデータを含めてください。

例：
```
@ボット名 /data https://yoursite.com
表示 URL ドメイン,インプレッション シェア
yoursite.com,42.5%
competitor1.com,31.8%
competitor2.net,15.6%
competitor3.jp,5.2%
competitor4.co.jp,3.1%
competitor5.com,1.8%
competitor6.com,0.9%
```

- 先頭行に自社のURLを指定すると、自社LPの分析も行われます
- CSVデータはカンマ区切りで、ドメインとシェアのペアを記述してください
- 少なくとも2つ以上のドメインを指定してください
"""
                        send_slack_message(channel, help_message, thread_ts)
                    else:
                        send_slack_message(channel, "ランディングページを分析するには、URLを送信してください。例: https://example.com", thread_ts)
                    
                    return {"status": "error", "message": "必要なデータが提供されていません"}
                
            except Exception as e:
                error_message = f"処理中にエラーが発生しました: {str(e)}\n{traceback.format_exc()}"
                logger.error(error_message)
                send_slack_message(channel, f"エラーが発生しました: {str(e)}", thread_ts)
                return {"status": "error", "message": str(e)}
        
        return {"status": "Event not handled", "event_type": event_type}
        
        
    except Exception as e:
        logger.error(f"Slackイベント処理エラー: {str(e)}")
        traceback.print_exc()
        return {"status": "error", "message": str(e)}, 500

def generate_interim_report(query, search_data):
    """第1フェーズの検索データから中間レポートを生成する"""
    try:
        # VertexAIが初期化されていない場合は初期化
        if gemini_model is None:
            init_vertexai()
        
        # 検索データから情報を抽出
        keywords = search_data.get("keywords", [])
        total_sites = sum(len(kw.get("results", [])) for kw in keywords)
        
        # 各キーワードと結果を文字列形式でまとめる
        sources_text = ""
        
        for i, keyword_data in enumerate(keywords, 1):
            keyword = keyword_data.get("keyword", "")
            results = keyword_data.get("results", [])
            
            if not results:
                continue
                
            sources_text += f"\n## キーワード {i}: {keyword}\n\n"
            
            for j, site in enumerate(results, 1):
                if site.get("status") != "success":
                    continue
                    
                title = site.get("title", "タイトルなし")
                url = site.get("url", "URLなし")
                content = site.get("content", "内容なし")
                
                # コンテンツの抽出（長すぎる場合でも切り捨てない）
                
                sources_text += f"### ソース {j}: {title}\n"
                sources_text += f"URL: {url}\n"
                sources_text += f"内容: {content}\n\n"
        
        # 中間レポート生成プロンプト
        prompt = f"""
あなたは情報整理の専門家です。
以下の質問に対して、提供された情報源から簡潔な中間レポートを作成してください：

質問: {query}

以下の情報源からの内容を要約してください：
{sources_text}

この中間レポートの要件：
1. 情報源から得られた主要なポイントを整理する
2. 質問に関連する重要な情報を抽出する
3. 簡潔に要約する（詳細な分析は最終レポートで行う）
4. 具体的なデータや事実を含める
5. 各情報の出典を記録する

中間レポートの長さは1000語程度を目標としてください。
このレポートは第2フェーズの検索キーワード生成に使用されます。
"""
        
        # AIで中間レポート生成
        response = gemini_model.generate_content(prompt)
        report = response.text
        
        logger.info(f"中間レポート生成完了: {len(report)}文字")
        return report
        
    except Exception as e:
        logger.error(f"中間レポート生成エラー: {str(e)}")
        traceback.print_exc()
        return f"中間レポート生成中にエラーが発生しました。エラー詳細: {str(e)}"

def generate_comprehensive_report(query, search_data):
    """検索データを元に包括的なレポートを生成する"""
    try:
        # VertexAIが初期化されていない場合は初期化
        if gemini_model is None:
            init_vertexai()
        
        # 検索データから情報を抽出
        keywords = search_data.get("keywords", [])
        total_sites = sum(len(kw.get("results", [])) for kw in keywords)
        
        # 各キーワードと結果を文字列形式でまとめる
        sources_text = ""
        total_tokens = 0
        max_tokens_per_source = 10000  # 各ソースの最大トークン数（概算）
        max_total_tokens = 900000  # 全ソーステキストの最大トークン数（余裕を持たせる）
        
        # 処理したサイト数をカウント
        processed_sites = 0
        
        # ソース情報を保存するリスト
        source_info = []
        source_counter = 1
        
        for i, keyword_data in enumerate(keywords, 1):
            keyword = keyword_data.get("keyword", "")
            results = keyword_data.get("results", [])
            
            if not results:
                continue
                
            keyword_header = f"\n## キーワード {i}: {keyword}\n\n"
            # キーワードヘッダーの概算トークン数
            keyword_tokens = len(keyword_header.split())
            
            if total_tokens + keyword_tokens > max_total_tokens:
                logger.warning(f"トークン制限に達したため、残りのキーワードを省略します（{i}/{len(keywords)}）")
                break
                
            sources_text += keyword_header
            total_tokens += keyword_tokens
                
            for j, site in enumerate(results, 1):
                if site.get("status") != "success":
                    continue
                
                title = site.get("title", "タイトルなし")
                url = site.get("url", "URLなし")
                content = site.get("content", "内容なし")
                
                # コンテンツのトークン数を概算（空白で分割して単語数をカウント）
                content_tokens = len(content.split())
                
                # トークン数が多すぎる場合は切り詰める
                if content_tokens > max_tokens_per_source:
                    logger.info(f"コンテンツが長すぎるため切り詰めます: {url}（{content_tokens}トークン）")
                    # 単語数ベースで切り詰め（簡易的な方法）
                    words = content.split()
                    content = " ".join(words[:max_tokens_per_source])
                    content_tokens = max_tokens_per_source
                
                # ソースヘッダーとコンテンツのトークン数
                source_header = f"### ソース {source_counter}: {title}\nURL: {url}\n内容: "
                source_tokens = len(source_header.split()) + content_tokens
                
                # トークン制限を超える場合はこのソースをスキップ
                if total_tokens + source_tokens > max_total_tokens:
                    logger.warning(f"トークン制限に達したため、残りのソースを省略します（キーワード{i}の{j}/{len(results)}）")
                    break
                
                # ソース情報をリストに追加
                source_info.append({"num": source_counter, "title": title, "url": url})
                
                sources_text += f"### ソース {source_counter}: {title}\n"
                sources_text += f"URL: {url}\n"
                sources_text += f"内容: {content}\n\n"
                
                total_tokens += source_tokens
                processed_sites += 1
                source_counter += 1
        
        logger.info(f"レポート生成に使用するデータ: {processed_sites}サイト、約{total_tokens}トークン")
        
        # レポート生成プロンプト
        prompt = f"""
あなたは詳細で具体的な調査レポートを作成する専門家です。
以下の質問に対して、提供された情報源からできるだけ具体的で詳細な解像度の高いレポートを作成してください：

質問: {query}

【質問意図の深堀り】
まず、この質問の本質的な意図を深く分析してください。例えば：
- 「AIエージェントの軌跡と今後の進展」という質問であれば、主に「AIエージェントの歴史的発展」と「今後の進展予測」という2つの主要軸があります。
- ユーザーが本当に知りたいのは、過去の発展経緯と将来の方向性であり、特に将来予測に強い関心がある可能性が高いです。

質問の意図を1-2つの主要軸に整理し、それに沿ってレポートを構成してください。各主要軸について、情報源から得られた具体的な事実、データ、予測を詳細に展開してください。

以下の情報源を活用してレポートを作成してください：
{sources_text}

【重要な指示】
あなたの最も重要な役割は、情報源に書かれている内容を「抽象的な要約」ではなく、「具体的な詳細」まで掘り下げて説明することです。

以下の点を必ず順守してください：

1. 「〜のリスクがある」「〜の可能性がある」のような抽象的な表現だけで終わらせないこと
   - 不適切な例: 「AIの悪用リスクがある（ソース3）」
   - 適切な例: 「AIの悪用リスクとして、ソース3では具体的に、2023年にはディープフェイク技術を使った詐欺が前年比63%増加し、特にXYZ社のCEOを模倣した音声によって金融部門の従業員が580万ドルを詐取された事例が報告されている。また同ソースでは...」

2. レポートの構成は質問の主要軸に沿って組み立てること
   - 例えば「AIエージェントの軌跡と今後の進展」という質問には：
     1. AIエージェントの歴史と発展（重要な節目、技術的ブレークスルー）
     2. 現在のAIエージェントの状況（主要企業、技術レベル）
     3. 未来の発展予測（短期・中期・長期の予測）
     4. 産業や社会への影響（将来の変化）
   というような構成が適切です。

3. 質問の主要軸に関連する具体的なデータを優先すること
   - 歴史に関する質問には、重要な時系列の出来事や転換点
   - 未来予測に関する質問には、具体的な数値予測や専門家の見解
   - 技術に関する質問には、具体的な仕組みや方法論の詳細
   を重点的に含めること

4. 各ソースから得られる具体的な情報を詳細に展開
   - 数値データ（統計、パーセンテージ、金額、日付など）
   - 具体的な事例（実際に起きた出来事、企業や組織の取り組み）
   - 専門家の具体的な見解（名前と役職を含む）
   - 技術的な詳細（仕組み、プロセス、方法論）

5. 情報を統合する際も具体性を保持し、質問の主要軸から外れないこと
   - 複数のソースで似た情報がある場合、それぞれの具体的な違いや共通点を明示
   - 質問の主要軸に関係の薄い情報は簡潔に触れるにとどめる

6. レポートの分量は主要軸の充実度を優先し、字数制限は設けません
   - 質問の主要軸に関する情報が豊富にあれば、その部分を十分に展開してください
   - 周辺情報より、質問の核心に関する情報を優先的に詳細化してください

具体的な表現例（同じ内容でも表現方法が異なります）：
- 不適切（抽象的）：「AIによる雇用への影響が懸念されています（ソース1）」
- 適切（具体的）：「ソース1によれば、McKinseyの2023年の調査では、現在の技術でAIが代替可能な仕事は全職業の約27%に達し、特に金融分野では事務処理業務の60%が2030年までに自動化される可能性があるとしています。同調査では具体的な職種として、データ入力作業員（代替率91%）、会計士（代替率76%）、カスタマーサービス（代替率52%）が高リスク職種として挙げられています。一方でAIによる新規雇用創出効果については...」

このレポートは「質問の主要軸について、次に質問する必要がないほど詳細で具体的」であることを目指してください。
質問の意図に沿った情報を優先的に詳細化し、ユーザーの知りたい核心部分に焦点を当てたレポートを作成してください。

最後に、レポートの末尾に「参考文献」セクションを追加して、使用したすべての情報源のリストを示してください。
"""
        
        # AIでレポート生成
        response = gemini_model.generate_content(prompt)
        report = response.text
        
        # レポートに参考文献リストが含まれていない場合は追加
        if "参考文献" not in report.lower():
            sources_list = "\n\n## 参考文献\n"
            for source in source_info:
                sources_list += f"ソース{source['num']}: 「{source['title']}」 - {source['url']}\n"
            report += sources_list
        
        logger.info(f"最終レポート生成完了: {len(report)}文字")
        return report
        
    except Exception as e:
        logger.error(f"レポート生成エラー: {str(e)}")
        traceback.print_exc()
        return f"レポート生成中にエラーが発生しました。エラー詳細: {str(e)}"

def initialize_selenium():
    """Seleniumを初期化する"""
    global selenium_initialized
    
    if selenium_initialized:
        return True
    
    try:
        logger.info("Selenium初期化を開始...")
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        
        # Dockerで設定された固定パスを使用
        chrome_bin = os.environ.get('CHROME_BIN', '/usr/bin/chromium')
        chromedriver_path = os.environ.get('CHROMEDRIVER_PATH', '/usr/bin/chromedriver')
        
        logger.info(f"Chrome Path: {chrome_bin}")
        logger.info(f"ChromeDriver Path: {chromedriver_path}")
        
        # 存在確認
        if not os.path.exists(chromedriver_path):
            logger.error(f"ChromeDriverが見つかりません: {chromedriver_path}")
            return False
            
        selenium_initialized = True
        logger.info("Selenium初期化完了")
        return True
    except Exception as e:
        logger.error(f"Selenium初期化エラー: {str(e)}")
        traceback.print_exc()
        return False

def search_duckduckgo(query):
    """DuckDuckGoで検索を実行し、結果を返す"""
    try:
        logger.info(f"検索開始: {query}")
        
        # Seleniumが初期化されていない場合は初期化
        if not initialize_selenium():
            return []
        
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException, NoSuchElementException
        import time
        
        # Chromeのオプション設定
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")  # 自動化検出を無効化
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        # Dockerで設定された固定パスを使用
        chrome_bin = os.environ.get('CHROME_BIN', '/usr/bin/chromium')
        chromedriver_path = os.environ.get('CHROMEDRIVER_PATH', '/usr/bin/chromedriver')
        
        chrome_options.binary_location = chrome_bin
        
        # WebDriverの設定
        service = Service(executable_path=chromedriver_path)
        
        logger.info("WebDriverを起動中...")
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        try:
            # DuckDuckGoのHTML版に直接アクセス
            encoded_query = quote_plus(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
            logger.info(f"DuckDuckGoにアクセス: {url}")
            
            driver.get(url)
            logger.info("ページ読み込み完了")
            
            # 少し待機してページが完全に読み込まれるのを確認
            time.sleep(2)
            
            # デバッグのためにページスクリーンショットを保存
            driver.save_screenshot('/tmp/duckduckgo_page.png')
            logger.info("スクリーンショット保存: /tmp/duckduckgo_page.png")
            
            # 提供されたHTMLに基づいて直接セレクタを指定
            try:
                # 検索結果コンテナを確認
                results_container = driver.find_element(By.CSS_SELECTOR, ".serp__results, #links, .results")
                logger.info("検索結果コンテナが見つかりました")
                
                # 各検索結果を取得
                result_elements = driver.find_elements(By.CSS_SELECTOR, ".result")
                logger.info(f"検索結果要素数: {len(result_elements)}")
                
                if not result_elements or len(result_elements) == 0:
                    logger.warning("検索結果が見つかりません。ページソースをチェックします")
                    with open('/tmp/page_source.html', 'w', encoding='utf-8') as f:
                        f.write(driver.page_source)
                    logger.info("ページソース保存: /tmp/page_source.html")
                    
                # 検索結果を抽出
                results = []
                for i, element in enumerate(result_elements[:10]):  # 上位10件のみ取得
                    try:
                        # タイトルとリンクを取得
                        title_link = element.find_element(By.CSS_SELECTOR, ".result__a")
                        title = title_link.text.strip()
                        link = title_link.get_attribute("href")
                        
                        # DuckDuckGoのリダイレクトリンクから実際のURLを抽出
                        if "duckduckgo.com/l/?uddg=" in link:
                            encoded_url = link.split("duckduckgo.com/l/?uddg=")[1].split("&")[0]
                            link = unquote(encoded_url)
                        
                        # スニペットを取得
                        snippet_element = element.find_element(By.CSS_SELECTOR, ".result__snippet")
                        snippet = snippet_element.text.strip()
                        
                        # 結果に追加
                        results.append({
                            "title": title,
                            "url": link,
                            "snippet": snippet
                        })
                        logger.info(f"結果 {i+1}: {title}")
                    except Exception as e:
                        logger.error(f"検索結果 {i+1} の解析エラー: {str(e)}")
                        continue
                
                logger.info(f"最終検索結果: {len(results)}件")
                return results
                
            except Exception as e:
                logger.error(f"検索結果の抽出エラー: {str(e)}")
                traceback.print_exc()
            
            # 検索結果が取得できなかった場合、ダミー結果を返す
            if not results:
                logger.info("ダミー結果を生成します")
                results = [{
                    "title": f"{query} に関する情報",
                    "url": f"https://duckduckgo.com/?q={encoded_query}",
                    "snippet": "検索システムから結果を取得できませんでした。ブラウザで直接検索してみてください。"
                }]
                
            return results
            
        finally:
            # ブラウザを閉じる
            try:
                driver.quit()
                logger.info("WebDriverを終了")
            except Exception as e:
                logger.error(f"WebDriver終了エラー: {str(e)}")
    
    except Exception as e:
        logger.error(f"検索エラー: {str(e)}")
        traceback.print_exc()
        return []

def fetch_website_content(url):
    """ウェブサイトのコンテンツとメタ情報を取得する"""
    try:
        logger.info(f"ウェブサイトコンテンツの取得開始: {url}")
        
        # ユーザーエージェントを設定
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        # タイムアウト設定（秒）
        timeout = 10
        
        # GETリクエスト
        response = requests.get(url, headers=headers, timeout=timeout)
        
        # エンコーディングを確認
        if response.encoding == 'ISO-8859-1':
            # 日本語コンテンツの場合、UTF-8を試みる
            response.encoding = 'utf-8'
        
        # ステータスコードをチェック
        if response.status_code != 200:
            logger.warning(f"HTTPエラー: {response.status_code} - {url}")
            return {
                "status": "error",
                "status_code": response.status_code,
                "url": url,
                "message": f"HTTPエラー {response.status_code}"
            }
        
        # HTMLを解析
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # タイトルを取得
        title = soup.title.string.strip() if soup.title else "タイトルなし"
        
        # メタ情報を取得
        meta_data = {}
        for meta in soup.find_all('meta'):
            name = meta.get('name') or meta.get('property')
            content = meta.get('content')
            if name and content:
                meta_data[name] = content
        
        # 本文テキストを取得（scriptとstyleタグを除外）
        for script in soup(["script", "style"]):
            script.extract()
        
        text = soup.get_text(separator="\n", strip=True)
        
        # テキストを整形（空行を削除、複数の改行を1つにまとめる）
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned_text = "\n".join(lines)
        
        # 長すぎるテキストは切り詰めない（全内容を保存）
        
        # 結果をJSONとして返す
        result = {
            "status": "success",
            "url": url,
            "title": title,
            "meta_data": meta_data,
            "content": cleaned_text,
            "content_length": len(cleaned_text)
        }
        
        logger.info(f"ウェブサイトコンテンツの取得完了: {url}")
        return result
        
    except requests.exceptions.Timeout:
        logger.error(f"リクエストタイムアウト: {url}")
        return {
            "status": "error",
            "url": url,
            "message": "リクエストタイムアウト"
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"リクエストエラー: {url} - {str(e)}")
        return {
            "status": "error",
            "url": url,
            "message": f"リクエストエラー: {str(e)}"
        }
    except Exception as e:
        logger.error(f"ウェブサイトコンテンツ取得エラー: {url} - {str(e)}")
        traceback.print_exc()
        return {
            "status": "error",
            "url": url,
            "message": f"コンテンツ取得エラー: {str(e)}"
        }

def analyze_search_results(results, max_sites=5):
    """検索結果の上位サイトにアクセスしてコンテンツを取得・分析する"""
    if not results or len(results) == 0:
        return {
            "status": "error",
            "message": "検索結果がありません"
        }
    
    # 上位N件のサイトのみ処理
    sites_to_process = min(len(results), max_sites)
    analyzed_results = []
    
    for i, result in enumerate(results[:sites_to_process]):
        url = result.get("url")
        if not url or "duckduckgo.com" in url:
            continue
            
        logger.info(f"サイト {i+1}/{sites_to_process} 分析中: {url}")
        
        # サイトのコンテンツを取得
        content_data = fetch_website_content(url)
        
        # 元の検索結果情報と合わせる
        content_data.update({
            "search_title": result.get("title"),
            "search_snippet": result.get("snippet")
        })
        
        analyzed_results.append(content_data)
        
        # サーバー負荷軽減のため少し待機
        if i < sites_to_process - 1:  # 最後のサイト以外は待機
            time.sleep(1)
    
    return {
        "status": "success",
        "query_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(analyzed_results),
        "results": analyzed_results
    }

def init_vertexai():
    """VertexAIモデルを初期化する"""
    global gemini_model
    
    try:
        if gemini_model is not None:
            return
            
        logger.info("VertexAI初期化を開始...")
        from google.cloud import aiplatform
        from vertexai.generative_models import GenerativeModel
        
        # VertexAIの初期化
        aiplatform.init(project=PROJECT_ID, location=LOCATION)
        
        # Geminiモデルの初期化
        gemini_model = GenerativeModel("gemini-2.0-flash-001")
        logger.info("VertexAI初期化完了")
    except Exception as e:
        logger.error(f"VertexAI初期化エラー: {str(e)}")
        traceback.print_exc()
        raise e

def init_google_sheets():
    """Google Sheets APIを初期化する"""
    global sheets_service
    
    try:
        if sheets_service is not None:
            return sheets_service
            
        logger.info("Google Sheets API初期化を開始...")
        
        # 認証情報の設定
        credentials_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        
        # Sheets APIクライアントの作成
        sheets_service = build('sheets', 'v4', credentials=credentials)
        logger.info("Google Sheets API初期化完了")
        return sheets_service
        
    except Exception as e:
        logger.error(f"Google Sheets API初期化エラー: {str(e)}")
        traceback.print_exc()
        return None

def generate_executive_summary(original_url, original_analysis, similar_analyses_data, full_report=None):
    """
    分析結果のエグゼクティブサマリーを生成する
    """
    try:
        logger.info("エグゼクティブサマリーの生成を開始")
        
        # full_reportが渡された場合はそちらを優先して使用
        if full_report:
            prompt = f"""
あなたはマーケティングコンサルタントです。以下のランディングページ（LP）分析レポートから、詳細かつ構造化されたエグゼクティブサマリーを作成してください。
これはクライアントへの提案資料の冒頭に使用されるものです。具体的で分かりやすく、重要なポイントを強調し、ビジネス判断に直接役立つ情報を提供してください。

### 分析レポート全文
{full_report[:7000]}  # トークン制限を考慮

以下の構造に沿ってエグゼクティブサマリーを作成してください。各セクションは具体的な数値や特徴を含め、マークダウン形式で整形してください：

## 1. LP基本情報と総評

### コンテンツ構成と差別化ポイント
- **メイン訴求**: [具体的な訴求内容と数値]
- **提供価値**: [提供価値の具体的な内容]
- **独自の特徴**: [競合にない特徴]

### デザインとUXの特徴

### ターゲティングと訴求戦略
- **ターゲット**: [具体的なターゲット像]
- **信頼性担保**: [実績、資格、メディア掲載などの具体的数値]
- **価格戦略**: [価格設定、割引方法の特徴]

### 全体的な総評
- **最も効果的**: [最も効果的な部分とその理由]
- **最も改善点**: [改善すべき部分と改善案]
- **与える印象**: [LPが訴求対象に与える全体的な印象]
- **一言表現**: [LPを一言で表現]

## 2. 類似LP比較分析

### 顧客ターゲットの違い
表形式で、自社LPと競合LP（上位3-4社）のターゲット層の違いを簡潔に比較してください。

### アピールポイントの差異
表形式で、自社LPと競合LP（上位3-4社）の主要アピールポイントの違いを比較してください。

### 競合LPからの学び
- **取り入れるべき要素**: [競合から学ぶべき5つの要素]
- **差別化すべき要素**: [自社の強みを活かした差別化ポイント5つ]

## 3. ユーザー口コミ分析

### 口コミ全体傾向
[平均評価、投稿数、ポジネガ比率などの具体的数値]

### ポジティブな評価ポイント
上位3-5つのポジティブポイントを具体的に列挙

### ネガティブな評価ポイント
上位3つのネガティブポイントを具体的に列挙

### ユーザーのペインポイント
- **利用前の不安**: [具体的な不安要素]
- **満足した点**: [具体的な満足ポイント]
- **改善希望点**: [具体的な改善要望]

## 4. 3C分析

### 顧客（Customer）
- **属性**: [具体的な属性]
- **ペイン**: [具体的な悩み・問題点]
- **ゲイン**: [具体的な得られるメリット]
- **ジョブ**: [達成したいこと]

### 競合（Competitor）
- **顕在競合**: [直接的競合の定義と具体例]
- **潜在競合**: [間接的競合の定義と具体例]
- **競合優位点**: [競合の強み]
- **競合弱点**: [競合の弱み]

### 自社（Company）
- **強み・USP**: [明確な強み・USP]
- **主張**: [サービス提供者が主張している強み]
- **最も魅力的な点**: [3つの魅力的なポイント]

## 5. 訴求仮説と改善提案

### 効果的な訴求仮説
具体的な訴求メッセージと根拠、期待効果を3つ提案

### 具体的な改善提案
具体的な改善提案を3つ提示

以上のセクションを明確に構造化し、具体的な数値やキーワードを含めて作成してください。
全体の情報量を保ちながら、見やすく整理されたマークダウン形式で出力してください。
特に「一般的」「効果的」などの曖昧な表現は避け、具体的な特徴や数値で表現してください。
"""
            
            # AIでサマリー生成
            summary = generate_ai_response(prompt)
            logger.info(f"フルレポートからエグゼクティブサマリー生成完了: {len(summary)}文字")
            
            return summary
        
        # 従来の方法（互換性のため残す）
        original_title = original_analysis.get('title', 'タイトルなし')
        
        # 類似LPの情報を収集
        similar_lps_info = []
        if similar_analyses_data and 'similar_lps' in similar_analyses_data:
            for lp in similar_analyses_data['similar_lps']:
                similar_lps_info.append({
                    'url': lp.get('url', ''),
                    'title': lp.get('title', 'タイトルなし'),
                    'service_name': lp.get('service_name', ''),
                    'impression_share': lp.get('impression_share', 'N/A')
                })
        
        # Geminiでサマリー生成
        prompt = f"""
あなたはマーケティングコンサルタントです。以下のランディングページ（LP）分析結果から、詳細かつ構造化されたエグゼクティブサマリーを作成してください。
これはクライアントへの提案資料の冒頭に使用されるものです。具体的で分かりやすく、重要なポイントを強調し、ビジネス判断に直接役立つ情報を提供してください。

### 分析対象LP
URL: {original_url}
タイトル: {original_title}

### 分析概要
{json.dumps(original_analysis.get('analysis', ''), ensure_ascii=False, default=str)[:3000]}

### 競合LP情報
{json.dumps(similar_lps_info, ensure_ascii=False, default=str)}

以下の構造に沿ってエグゼクティブサマリーを作成してください。各セクションは具体的な数値や特徴を含め、マークダウン形式で整形してください：

## 1. LP基本情報と総評

### コンテンツ構成と差別化ポイント
- **メイン訴求**: [具体的な訴求内容と数値]
- **提供価値**: [提供価値の具体的な内容]
- **独自の特徴**: [競合にない特徴]

### デザインとUXの特徴

### ターゲティングと訴求戦略
- **ターゲット**: [具体的なターゲット像]
- **信頼性担保**: [実績、資格、メディア掲載などの具体的数値]
- **価格戦略**: [価格設定、割引方法の特徴]

### 全体的な総評
- **最も効果的**: [最も効果的な部分とその理由]
- **最も改善点**: [改善すべき部分と改善案]
- **与える印象**: [LPが訴求対象に与える全体的な印象]
- **一言表現**: [LPを一言で表現]

## 2. 類似LP比較分析

### 顧客ターゲットの違い
表形式で、自社LPと競合LP（上位3-4社）のターゲット層の違いを簡潔に比較してください。

### アピールポイントの差異
表形式で、自社LPと競合LP（上位3-4社）の主要アピールポイントの違いを比較してください。

### 競合LPからの学び
- **取り入れるべき要素**: [競合から学ぶべき5つの要素]
- **差別化すべき要素**: [自社の強みを活かした差別化ポイント5つ]

## 3. ユーザー口コミ分析

### 口コミ全体傾向
[平均評価、投稿数、ポジネガ比率などの具体的数値]

### ポジティブな評価ポイント
上位3-5つのポジティブポイントを具体的に列挙

### ネガティブな評価ポイント
上位3つのネガティブポイントを具体的に列挙

### ユーザーのペインポイント
- **利用前の不安**: [具体的な不安要素]
- **満足した点**: [具体的な満足ポイント]
- **改善希望点**: [具体的な改善要望]

## 4. 3C分析

### 顧客（Customer）
- **属性**: [具体的な属性]
- **ペイン**: [具体的な悩み・問題点]
- **ゲイン**: [具体的な得られるメリット]
- **ジョブ**: [達成したいこと]

### 競合（Competitor）
- **顕在競合**: [直接的競合の定義と具体例]
- **潜在競合**: [間接的競合の定義と具体例]
- **競合優位点**: [競合の強み]
- **競合弱点**: [競合の弱み]

### 自社（Company）
- **強み・USP**: [明確な強み・USP]
- **主張**: [サービス提供者が主張している強み]
- **最も魅力的な点**: [3つの魅力的なポイント]

## 5. 訴求仮説と改善提案

### 効果的な訴求仮説
具体的な訴求メッセージと根拠、期待効果を3つ提案

### 具体的な改善提案
具体的な改善提案を3つ提示

以上のセクションを明確に構造化し、具体的な数値やキーワードを含めて作成してください。
全体の情報量を保ちながら、見やすく整理されたマークダウン形式で出力してください。
特に「一般的」「効果的」などの曖昧な表現は避け、具体的な特徴や数値で表現してください。
"""
        
        # AIでサマリー生成
        summary = generate_ai_response(prompt)
        logger.info(f"エグゼクティブサマリー生成完了: {len(summary)}文字")
        
        return summary
    except Exception as e:
        logger.error(f"エグゼクティブサマリー生成エラー: {str(e)}")
        traceback.print_exc()
        return "エグゼクティブサマリーの生成中にエラーが発生しました。詳細な分析結果を参照してください。"

def create_3c_analysis_spreadsheet(original_url, full_report):
    """完全なレポートを既存のスプレッドシートに新しいシートとして保存する"""
    try:
        # Google Sheets APIサービスの初期化
        sheets_service = init_google_sheets()
        if not sheets_service:
            logger.error("Google Sheets APIの初期化に失敗しました。")
            return None, None

        # 指定されたスプレッドシートIDを使用
        spreadsheet_id = "1cmUw6EbQ3Cykvvl04JYBFevIK0MoitEMcXA_NNLcWmM"
        logger.info(f"指定されたスプレッドシートを使用します: {spreadsheet_id}")

        # URLからシート名を生成
        domain = re.sub(r'^https?://(www\.)?', '', original_url)
        domain = re.sub(r'[^\w\s-]', '', domain).strip()
        domain = re.sub(r'[-\s]+', '-', domain)
        # 現在の日時を追加
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        sheet_name = f"{domain}_{timestamp}"
        if len(sheet_name) > 100:  # シート名の長さ制限
            sheet_name = sheet_name[:97] + "..."

        # 新しいシートを追加
        add_sheet_request = {
            'addSheet': {
                'properties': {
                    'title': sheet_name,
                    'gridProperties': {
                        'rowCount': 1000,
                        'columnCount': 10
                    }
                }
            }
        }
        
        response = sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': [add_sheet_request]}
        ).execute()
        
        # 新しいシートのIDを取得
        sheet_id = response['replies'][0]['addSheet']['properties']['sheetId']
        logger.info(f"新しいシートを追加しました: {sheet_name} (ID: {sheet_id})")

        # エグゼクティブサマリーの生成 - フルレポートをそのまま渡す
        executive_summary = None
        try:
            # URLの抽出（必要な場合）
            url_match = re.search(r'分析対象URL:\s*(https?://[^\s]+)', full_report)
            if url_match:
                original_url = url_match.group(1)
            
            # フルレポートを直接渡す（他のパラメータは空で良い）
            executive_summary = generate_executive_summary(original_url, {}, {}, full_report=full_report)
            logger.info(f"エグゼクティブサマリー生成完了")
        except Exception as e:
            logger.error(f"エグゼクティブサマリー生成中にエラーが発生: {str(e)}")
            executive_summary = "エグゼクティブサマリーの生成に失敗しました。詳細な分析結果をご確認ください。"

        # レポートのマークダウンをパースしてスプレッドシートに書き込む
        data = []  # 書き込むデータ
        formatting_requests = []  # 書式設定リクエスト
        row_types = []  # 各行のタイプを追跡
        row_index = 0
        is_table_header = False
        is_in_table = False
        table_headers = []
        table_rows = []  # テーブルの行インデックスを記録
        table_sections = []  # テーブルのセクション（開始行、終了行、列数）を記録
        current_table = None  # 現在処理中のテーブル情報
        qa_rows = []  # Q&A形式の行インデックスを記録
        
        # エグゼクティブサマリーをデータの先頭に追加
        if executive_summary:
            # サマリータイトル
            data.append(["エグゼクティブサマリー"])
            row_types.append('h1')
            formatting_requests.append({
                'repeatCell': {
                    'range': {
                        'sheetId': sheet_id,
                        'startRowIndex': row_index,
                        'endRowIndex': row_index + 1,
                        'startColumnIndex': 0,
                        'endColumnIndex': 5
                    },
                    'cell': {
                        'userEnteredFormat': {
                            'backgroundColor': {'red': 0.2, 'green': 0.3, 'blue': 0.6},  # 濃い青色
                            'textFormat': {
                                'fontSize': 16,
                                'bold': True,
                                'foregroundColor': {'red': 1.0, 'green': 1.0, 'blue': 1.0}  # 白文字
                            },
                            'horizontalAlignment': 'CENTER',
                            'verticalAlignment': 'MIDDLE'
                        }
                    },
                    'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)'
                }
            })
            # セルの結合
            formatting_requests.append({
                'mergeCells': {
                    'range': {
                        'sheetId': sheet_id,
                        'startRowIndex': row_index,
                        'endRowIndex': row_index + 1,
                        'startColumnIndex': 0,
                        'endColumnIndex': 5
                    },
                    'mergeType': 'MERGE_ALL'
                }
            })
            row_index += 1
            
            # 空行
            data.append([""])
            row_types.append('empty')
            row_index += 1
            
            # サマリー内容
            # 行ごとに分割して追加
            summary_lines = executive_summary.split('\n')
            for line in summary_lines:
                line = line.strip()
                if not line:
                    # 空行
                    data.append([""])
                    row_types.append('empty')
                else:
                    # 見出しかどうかをチェック
                    if line.startswith('#'):
                        # 見出し
                        heading_level = line.count('#')
                        text = line.lstrip('#').strip()
                        data.append([text])
                        
                        if heading_level == 1:
                            row_types.append('executive_h1')
                            # H1書式
                            formatting_requests.append({
                                'repeatCell': {
                                    'range': {
                                        'sheetId': sheet_id,
                                        'startRowIndex': row_index,
                                        'endRowIndex': row_index + 1,
                                        'startColumnIndex': 0,
                                        'endColumnIndex': 5
                                    },
                                    'cell': {
                                        'userEnteredFormat': {
                                            'backgroundColor': {'red': 0.8, 'green': 0.8, 'blue': 0.9},  # 薄い青色
                                            'textFormat': {
                                                'fontSize': 14,
                                                'bold': True
                                            },
                                            'horizontalAlignment': 'LEFT',
                                            'verticalAlignment': 'MIDDLE'
                                        }
                                    },
                                    'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)'
                                }
                            })
                            # セルの結合
                            formatting_requests.append({
                                'mergeCells': {
                                    'range': {
                                        'sheetId': sheet_id,
                                        'startRowIndex': row_index,
                                        'endRowIndex': row_index + 1,
                                        'startColumnIndex': 0,
                                        'endColumnIndex': 5
                                    },
                                    'mergeType': 'MERGE_ALL'
                                }
                            })
                        else:
                            row_types.append('executive_h2')
                            # H2書式
                            formatting_requests.append({
                                'repeatCell': {
                                    'range': {
                                        'sheetId': sheet_id,
                                        'startRowIndex': row_index,
                                        'endRowIndex': row_index + 1,
                                        'startColumnIndex': 0,
                                        'endColumnIndex': 5
                                    },
                                    'cell': {
                                        'userEnteredFormat': {
                                            'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.95},  # さらに薄い青色
                                            'textFormat': {
                                                'fontSize': 12,
                                                'bold': True
                                            },
                                            'horizontalAlignment': 'LEFT',
                                            'verticalAlignment': 'MIDDLE'
                                        }
                                    },
                                    'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)'
                                }
                            })
                            # セルの結合
                            formatting_requests.append({
                                'mergeCells': {
                                    'range': {
                                        'sheetId': sheet_id,
                                        'startRowIndex': row_index,
                                        'endRowIndex': row_index + 1,
                                        'startColumnIndex': 0,
                                        'endColumnIndex': 5
                                    },
                                    'mergeType': 'MERGE_ALL'
                                }
                            })
                    elif line.startswith('*') or line.startswith('-'):
                        # 箇条書き
                        text = line.lstrip('*- ').strip()
                        data.append([" • " + text])
                        row_types.append('bullet')
                        # 箇条書き書式
                        formatting_requests.append({
                            'repeatCell': {
                                'range': {
                                    'sheetId': sheet_id,
                                    'startRowIndex': row_index,
                                    'endRowIndex': row_index + 1,
                                    'startColumnIndex': 0,
                                    'endColumnIndex': 5
                                },
                                'cell': {
                                    'userEnteredFormat': {
                                        'textFormat': {
                                            'fontSize': 11
                                        },
                                        'horizontalAlignment': 'LEFT',
                                        'verticalAlignment': 'MIDDLE'
                                    }
                                },
                                'fields': 'userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment)'
                            }
                        })
                        # セルの結合
                        formatting_requests.append({
                            'mergeCells': {
                                'range': {
                                    'sheetId': sheet_id,
                                    'startRowIndex': row_index,
                                    'endRowIndex': row_index + 1,
                                    'startColumnIndex': 0,
                                    'endColumnIndex': 5
                                },
                                'mergeType': 'MERGE_ALL'
                            }
                        })
                    else:
                        # 通常段落
                        data.append([line])
                        row_types.append('executive_paragraph')
                        # 段落書式
                        formatting_requests.append({
                            'repeatCell': {
                                'range': {
                                    'sheetId': sheet_id,
                                    'startRowIndex': row_index,
                                    'endRowIndex': row_index + 1,
                                    'startColumnIndex': 0,
                                    'endColumnIndex': 5
                                },
                                'cell': {
                                    'userEnteredFormat': {
                                        'textFormat': {
                                            'fontSize': 11
                                        },
                                        'horizontalAlignment': 'LEFT',
                                        'verticalAlignment': 'MIDDLE',
                                        'wrapStrategy': 'WRAP'
                                    }
                                },
                                'fields': 'userEnteredFormat(textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)'
                            }
                        })
                        # セルの結合
                        formatting_requests.append({
                            'mergeCells': {
                                'range': {
                                    'sheetId': sheet_id,
                                    'startRowIndex': row_index,
                                    'endRowIndex': row_index + 1,
                                    'startColumnIndex': 0,
                                    'endColumnIndex': 5
                                },
                                'mergeType': 'MERGE_ALL'
                            }
                        })
                row_index += 1
            
            # 区切り線
            data.append([""])
            row_types.append('divider')
            # 区切り線の書式
            formatting_requests.append({
                'repeatCell': {
                    'range': {
                        'sheetId': sheet_id,
                        'startRowIndex': row_index,
                        'endRowIndex': row_index + 1,
                        'startColumnIndex': 0,
                        'endColumnIndex': 5
                    },
                    'cell': {
                        'userEnteredFormat': {
                            'borders': {
                                'bottom': {
                                    'style': 'SOLID',
                                    'width': 2,
                                    'color': {'red': 0.5, 'green': 0.5, 'blue': 0.5}
                                }
                            }
                        }
                    },
                    'fields': 'userEnteredFormat.borders.bottom'
                }
            })
            # セルの結合
            formatting_requests.append({
                'mergeCells': {
                    'range': {
                        'sheetId': sheet_id,
                        'startRowIndex': row_index,
                        'endRowIndex': row_index + 1,
                        'startColumnIndex': 0,
                        'endColumnIndex': 5
                    },
                    'mergeType': 'MERGE_ALL'
                }
            })
            row_index += 1
            
            # 空行をもう一つ追加
            data.append([""])
            row_types.append('empty')
            row_index += 1
        
        # 元のレポートの処理を追加
        lines = full_report.split('\n')
        
        # 各行をシートに書き込む形式に変換
        for line in lines:
            line = line.strip()
            # スキップすべき行
            if line.startswith('```') or not line:
                if not line:
                    # 空行を追加（セルは空にする）
                    data.append([''])
                    row_types.append('empty')
                    row_index += 1
                continue
                
            # 見出し処理
            if line.startswith('# '):
                # 大見出し（H1）- 太字、大きいフォント、オレンジ色の背景
                text = line.replace('# ', '')
                data.append([text])
                row_types.append('h1')
                formatting_requests.append({
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': row_index,
                            'endRowIndex': row_index + 1,
                            'startColumnIndex': 0,
                            'endColumnIndex': 5
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'backgroundColor': {'red': 0.95, 'green': 0.5, 'blue': 0.2},  # オレンジ色
                                'textFormat': {
                                    'fontSize': 14,
                                    'bold': True,
                                    'foregroundColor': {'red': 1.0, 'green': 1.0, 'blue': 1.0}  # 白文字
                                },
                                'horizontalAlignment': 'CENTER',
                                'verticalAlignment': 'MIDDLE'
                            }
                        },
                        'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)'
                    }
                })
                # セルの結合
                formatting_requests.append({
                    'mergeCells': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': row_index,
                            'endRowIndex': row_index + 1,
                            'startColumnIndex': 0,
                            'endColumnIndex': 5
                        },
                        'mergeType': 'MERGE_ALL'
                    }
                })
                row_index += 1
            elif line.startswith('## '):
                # 中見出し（H2）- 太字、青色の背景
                text = line.replace('## ', '')
                data.append([text])
                row_types.append('h2')
                formatting_requests.append({
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': row_index,
                            'endRowIndex': row_index + 1,
                            'startColumnIndex': 0,
                            'endColumnIndex': 5
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'backgroundColor': {'red': 0.2, 'green': 0.4, 'blue': 0.8},  # 青色
                                'textFormat': {
                                    'fontSize': 12,
                                    'bold': True,
                                    'foregroundColor': {'red': 1.0, 'green': 1.0, 'blue': 1.0}  # 白文字
                                },
                                'horizontalAlignment': 'CENTER',
                                'verticalAlignment': 'MIDDLE'
                            }
                        },
                        'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)'
                    }
                })
                # セルの結合
                formatting_requests.append({
                    'mergeCells': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': row_index,
                            'endRowIndex': row_index + 1,
                            'startColumnIndex': 0,
                            'endColumnIndex': 5
                        },
                        'mergeType': 'MERGE_ALL'
                    }
                })
                row_index += 1
            elif line.startswith('### '):
                # 小見出し（H3）- 太字、緑色の背景
                text = line.replace('### ', '')
                data.append([text])
                row_types.append('h3')
                formatting_requests.append({
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': row_index,
                            'endRowIndex': row_index + 1,
                            'startColumnIndex': 0,
                            'endColumnIndex': 5
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'backgroundColor': {'red': 0.2, 'green': 0.7, 'blue': 0.4},  # 緑色
                                'textFormat': {
                                    'bold': True,
                                    'foregroundColor': {'red': 1.0, 'green': 1.0, 'blue': 1.0}  # 白文字
                                },
                                'horizontalAlignment': 'CENTER',
                                'verticalAlignment': 'MIDDLE'
                            }
                        },
                        'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)'
                    }
                })
                # セルの結合
                formatting_requests.append({
                    'mergeCells': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': row_index,
                            'endRowIndex': row_index + 1,
                            'startColumnIndex': 0,
                            'endColumnIndex': 5
                        },
                        'mergeType': 'MERGE_ALL'
                    }
                })
                row_index += 1
            # URL行の特別処理
            elif line.startswith('URL:') or line.startswith('分析対象URL:'):
                parts = line.split(':', 1)
                label = parts[0].strip() + ':'
                url_value = parts[1].strip() if len(parts) > 1 else ''
                
                data.append([label, url_value])
                row_types.append('url_row')
                
                # URLラベルセルの書式設定（太字、背景色）
                formatting_requests.append({
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': row_index,
                            'endRowIndex': row_index + 1,
                            'startColumnIndex': 0,
                            'endColumnIndex': 1
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9},
                                'textFormat': {
                                    'bold': True
                                },
                                'verticalAlignment': 'MIDDLE'
                            }
                        },
                        'fields': 'userEnteredFormat(backgroundColor,textFormat,verticalAlignment)'
                    }
                })
                
                # URLの値にハイパーリンクを設定
                formatting_requests.append({
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': row_index,
                            'endRowIndex': row_index + 1,
                            'startColumnIndex': 1,
                            'endColumnIndex': 2
                        },
                        'cell': {
                            'userEnteredValue': {
                                'formulaValue': f'=HYPERLINK("{url_value}","{url_value}")'
                            }
                        },
                        'fields': 'userEnteredValue'
                    }
                })
                row_index += 1
            # 表のヘッダー行処理
            elif '|' in line and line.count('|') > 1 and line.count('-') > 3 and not is_table_header and not is_in_table:
                # 表の区切り行を検出（|----|----|----|）
                is_table_header = True
                continue
            # 表の行処理
            elif '|' in line and line.count('|') > 1:
                columns = [col.strip() for col in line.split('|')]
                # 最初と最後の空要素を削除
                if columns and not columns[0]:
                    columns = columns[1:]
                if columns and not columns[-1]:
                    columns = columns[:-1]
                
                if is_table_header:
                    # ヘッダー行として処理
                    table_headers = columns
                    data.append(columns)
                    row_types.append('table_header')
                    
                    # テーブルセクションの開始を記録
                    current_table = {
                        'start': row_index,
                        'cols': len(columns)
                    }
                    
                    # ヘッダーセルの書式設定
                    formatting_requests.append({
                        'repeatCell': {
                            'range': {
                                'sheetId': sheet_id,
                                'startRowIndex': row_index,
                                'endRowIndex': row_index + 1,
                                'startColumnIndex': 0,
                                'endColumnIndex': len(columns)
                            },
                            'cell': {
                                'userEnteredFormat': {
                                    'backgroundColor': {'red': 0.2, 'green': 0.4, 'blue': 0.8},  # 青色
                                    'textFormat': {
                                        'bold': True,
                                        'foregroundColor': {'red': 1.0, 'green': 1.0, 'blue': 1.0}  # 白文字
                                    },
                                    'horizontalAlignment': 'CENTER',
                                    'verticalAlignment': 'MIDDLE'
                                }
                            },
                            'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)'
                        }
                    })
                    is_table_header = False
                    is_in_table = True
                    row_index += 1
                else:
                    # テーブルの行の書式設定（データ行）
                    data.append(columns)
                    row_types.append('table_row')
                    
                    # 交互行のゼブラストライプ設定（偶数行に薄い色）
                    if is_in_table and (len(table_rows) % 2 == 1):
                        formatting_requests.append({
                            'repeatCell': {
                                'range': {
                                    'sheetId': sheet_id,
                                    'startRowIndex': row_index,
                                    'endRowIndex': row_index + 1,
                                    'startColumnIndex': 0,
                                    'endColumnIndex': len(columns)
                                },
                                'cell': {
                                    'userEnteredFormat': {
                                        'backgroundColor': {'red': 0.95, 'green': 0.95, 'blue': 1.0},  # 薄い青色背景
                                        'horizontalAlignment': 'LEFT',
                                        'verticalAlignment': 'TOP',
                                        'wrapStrategy': 'WRAP'
                                    }
                                },
                                'fields': 'userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,wrapStrategy)'
                            }
                        })
                    else:
                        # デフォルトのスタイル
                        formatting_requests.append({
                            'repeatCell': {
                                'range': {
                                    'sheetId': sheet_id,
                                    'startRowIndex': row_index,
                                    'endRowIndex': row_index + 1,
                                    'startColumnIndex': 0,
                                    'endColumnIndex': len(columns)
                                },
                                'cell': {
                                    'userEnteredFormat': {
                                        'horizontalAlignment': 'LEFT',
                                        'verticalAlignment': 'TOP',
                                        'wrapStrategy': 'WRAP'
                                    }
                                },
                                'fields': 'userEnteredFormat(horizontalAlignment,verticalAlignment,wrapStrategy)'
                            }
                        })
                    
                    if is_in_table:
                        table_rows.append(row_index)
                    row_index += 1
            else:
                # その他の通常テキスト行
                # マークダウン記法の変換
                text = line
                # 太字変換 (**text** -> text)
                text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
                # イタリック変換 (*text* -> text)
                text = re.sub(r'\*(.*?)\*', r'\1', text)
                # 箇条書き変換
                text = re.sub(r'- ', '• ', text)
                
                # 表の終了を検出
                if is_in_table and current_table:
                    current_table['end'] = row_index
                    table_sections.append(current_table)
                    current_table = None
                    is_in_table = False
                
                # 質問と回答を分けて表示する処理
                if ':' in text and not is_in_table:
                    parts = text.split(':', 1)
                    question = parts[0].strip() + ':'
                    answer = parts[1].strip() if len(parts) > 1 else ''
                    
                    # 質問をA列、回答をB列に配置
                    data.append([question, answer])
                    row_types.append('qa_row')
                    qa_rows.append(row_index)
                    row_index += 1
                else:
                    data.append([text])
                    row_types.append('normal')
                    row_index += 1
        
        # 表の終了を検出（最後の行が表の場合）
        if is_in_table and current_table:
            current_table['end'] = row_index
            table_sections.append(current_table)
        
        # 質問回答行のスタイル設定
        for i in qa_rows:
            # 質問セル（A列）の書式設定
            formatting_requests.append({
                'repeatCell': {
                    'range': {
                        'sheetId': sheet_id,
                        'startRowIndex': i,
                        'endRowIndex': i + 1,
                        'startColumnIndex': 0,
                        'endColumnIndex': 1
                    },
                    'cell': {
                        'userEnteredFormat': {
                            'backgroundColor': {'red': 0.85, 'green': 0.9, 'blue': 1.0},  # 薄い青色
                            'textFormat': {
                                'bold': True
                            },
                            'verticalAlignment': 'TOP'
                        }
                    },
                    'fields': 'userEnteredFormat(backgroundColor,textFormat,verticalAlignment)'
                }
            })
            
            # 回答セル（B列）の書式設定
            formatting_requests.append({
                'repeatCell': {
                    'range': {
                        'sheetId': sheet_id,
                        'startRowIndex': i,
                        'endRowIndex': i + 1,
                        'startColumnIndex': 1,
                        'endColumnIndex': 2
                    },
                    'cell': {
                        'userEnteredFormat': {
                            'verticalAlignment': 'TOP',
                            'wrapStrategy': 'WRAP'
                        }
                    },
                    'fields': 'userEnteredFormat(verticalAlignment,wrapStrategy)'
                }
            })
        
        # 行タイプに応じた高さを設定
        for i, row_type in enumerate(row_types):
            height = 80  # デフォルトの高さ
            
            if row_type == 'empty':
                height = 15  # 空行は小さく
            elif row_type == 'h1':
                height = 40  # 大見出し
            elif row_type == 'h2':
                height = 35  # 中見出し
            elif row_type == 'h3':
                height = 30  # 小見出し
            elif row_type == 'url':
                height = 30  # URL行
            elif row_type == 'table_header':
                height = 40  # テーブルヘッダー
            elif row_type == 'table_data':
                height = 90  # テーブルデータ（多めに確保）
            elif row_type == 'normal':
                # 通常の行の文字数によって高さを調整
                if i < len(data) and isinstance(data[i][0], str):
                    content_length = len(data[i][0])
                    if content_length < 50:  # 短いテキスト
                        height = 30
                    elif content_length < 150:  # 中程度のテキスト
                        height = 50
                    else:  # 長いテキスト
                        height = 100
            
            # 行の高さを設定
            formatting_requests.append({
                'updateDimensionProperties': {
                    'range': {
                        'sheetId': sheet_id,
                        'dimension': 'ROWS',
                        'startIndex': i,
                        'endIndex': i + 1
                    },
                    'properties': {
                        'pixelSize': height
                    },
                    'fields': 'pixelSize'
                }
            })
        
        # 全体のフォントとセル設定
        formatting_requests.append({
            'repeatCell': {
                'range': {
                    'sheetId': sheet_id,
                    'startRowIndex': 0,
                    'endRowIndex': row_index
                },
                'cell': {
                    'userEnteredFormat': {
                        'wrapStrategy': 'OVERFLOW_CELL',  # CLIPからOVERFLOW_CELLに変更（セルからはみ出す）
                        'verticalAlignment': 'TOP',
                        'textFormat': {
                            'fontSize': 11
                        },
                        'padding': {
                            'top': 8,
                            'right': 8,
                            'bottom': 8,
                            'left': 8
                        }
                    }
                },
                'fields': 'userEnteredFormat(wrapStrategy,verticalAlignment,textFormat,padding)'
            }
        })
        
        # QA行の書式設定
        qa_rows = []
        for i in range(row_index):
            if i < len(row_types) and row_types[i] == 'qa_row':
                qa_rows.append(i)
                
                # 質問セル（A列）の書式設定
                formatting_requests.append({
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': i,
                            'endRowIndex': i + 1,
                            'startColumnIndex': 0,
                            'endColumnIndex': 1
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'backgroundColor': {'red': 0.85, 'green': 0.9, 'blue': 1.0},  # 薄い青色
                                'textFormat': {
                                    'bold': True
                                },
                                'verticalAlignment': 'TOP'
                            }
                        },
                        'fields': 'userEnteredFormat(backgroundColor,textFormat,verticalAlignment)'
                    }
                })
                
                # 回答セル（B列）の書式設定
                formatting_requests.append({
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': i,
                            'endRowIndex': i + 1,
                            'startColumnIndex': 1,
                            'endColumnIndex': 2
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'verticalAlignment': 'TOP',
                                'wrapStrategy': 'WRAP'
                            }
                        },
                        'fields': 'userEnteredFormat(verticalAlignment,wrapStrategy)'
                    }
                })
        
        # テーブルヘッダー行のフォントと背景色を設定
        table_header_rows = []
        for i in range(row_index):
            if i < len(row_types) and row_types[i] == 'table_header':
                table_header_rows.append(i)
                formatting_requests.append({
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': i,
                            'endRowIndex': i + 1,
                            'startColumnIndex': 0,
                            'endColumnIndex': 10
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'backgroundColor': {'red': 0.7, 'green': 0.85, 'blue': 0.7},
                                'textFormat': {
                                    'bold': True
                                },
                                'horizontalAlignment': 'CENTER',
                                'verticalAlignment': 'MIDDLE',
                                'wrapStrategy': 'OVERFLOW_CELL'  # WRAPからOVERFLOW_CELLに変更
                            }
                        },
                        'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)'
                    }
                })

        # テーブルデータ行は折り返しあり → セルからはみ出すように変更
        for i in range(row_index):
            if i < len(row_types) and row_types[i] == 'table_data':
                formatting_requests.append({
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': i,
                            'endRowIndex': i + 1,
                            'startColumnIndex': 0,
                            'endColumnIndex': 10
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'wrapStrategy': 'OVERFLOW_CELL'  # WRAPからOVERFLOW_CELLに変更
                            }
                        },
                        'fields': 'userEnteredFormat(wrapStrategy)'
                    }
                })
                
        # 見出しの箇条書きをマークアップ（ステップなどの灰色背景）
        for i in range(row_index):
            if i < len(row_types) and i < len(data) and len(data[i]) > 0:
                # 【ステップx】のような行やh3の見出し（###で始まる）の背景色を変更
                if (isinstance(data[i][0], str) and 
                    (data[i][0].strip().startswith('【') or 
                     row_types[i] == 'h3' or 
                     data[i][0].strip().startswith('1.') or 
                     data[i][0].strip().startswith('2.') or 
                     data[i][0].strip().startswith('3.') or 
                     data[i][0].strip().startswith('4.') or 
                     data[i][0].strip().startswith('5.') or 
                     data[i][0].strip().startswith('6.'))):
                    
                    formatting_requests.append({
                        'repeatCell': {
                            'range': {
                                'sheetId': sheet_id,
                                'startRowIndex': i,
                                'endRowIndex': i + 1,
                                'startColumnIndex': 0,
                                'endColumnIndex': len(data[i]) if len(data[i]) > 0 else 1
                            },
                            'cell': {
                                'userEnteredFormat': {
                                    'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9},
                                    'textFormat': {
                                        'bold': True
                                    }
                                }
                            },
                            'fields': 'userEnteredFormat(backgroundColor,textFormat)'
                        }
                    })
        
        # 類似LP比較分析セクション用の処理を追加
        in_similar_lp_section = False
        similar_lp_rows = []
        
        # 類似LP比較分析セクションの行を特定
        for i in range(row_index):
            if i < len(data) and len(data[i]) > 0 and isinstance(data[i][0], str):
                # 「類似LP比較分析」という見出しを含む行を探す
                if '類似LP比較分析' in data[i][0]:
                    in_similar_lp_section = True
                    similar_lp_rows.append(i)
                # 次の大見出しが来たらセクション終了
                elif in_similar_lp_section and i < len(row_types) and (row_types[i] == 'h1' or row_types[i] == 'h2'):
                    in_similar_lp_section = False
                # 類似LP比較分析セクション内の行を記録
                elif in_similar_lp_section:
                    similar_lp_rows.append(i)
        
        # 類似LP比較分析セクションの行だけ折り返しを設定
        for i in similar_lp_rows:
            formatting_requests.append({
                'repeatCell': {
                    'range': {
                        'sheetId': sheet_id,
                        'startRowIndex': i,
                        'endRowIndex': i + 1,
                        'startColumnIndex': 0,
                        'endColumnIndex': 10
                    },
                    'cell': {
                        'userEnteredFormat': {
                            'wrapStrategy': 'WRAP'  # この特定のセクションだけWRAPに設定
                        }
                    },
                    'fields': 'userEnteredFormat(wrapStrategy)'
                }
            })
        
        # 行の高さを調整 - 行タイプに応じて高さを調整
        for i, row_type in enumerate(row_types):
            height = 80  # デフォルトの高さ
            
            if row_type == 'empty':
                height = 15  # 空行は小さく
            elif row_type == 'h1':
                height = 40  # 大見出し
            elif row_type == 'h2':
                height = 35  # 中見出し
            elif row_type == 'h3':
                height = 30  # 小見出し
            elif row_type == 'url':
                height = 30  # URL行
            elif row_type == 'table_header':
                height = 40  # テーブルヘッダー
            elif row_type == 'table_data':
                height = 90  # テーブルデータ（多めに確保）
            elif row_type == 'normal':
                # 通常の行の文字数によって高さを調整
                if i < len(data) and isinstance(data[i][0], str):
                    content_length = len(data[i][0])
                    if content_length < 50:  # 短いテキスト
                        height = 30
                    elif content_length < 150:  # 中程度のテキスト
                        height = 50
                    else:  # 長いテキスト
                        height = 100
            
            # 行の高さを設定
            formatting_requests.append({
                'updateDimensionProperties': {
                    'range': {
                        'sheetId': sheet_id,
                        'dimension': 'ROWS',
                        'startIndex': i,
                        'endIndex': i + 1
                    },
                    'properties': {
                        'pixelSize': height
                    },
                    'fields': 'pixelSize'
                }
            })
        
        # 罫線を設定 - 薄いグレーの罫線
        formatting_requests.append({
            'updateBorders': {
                'range': {
                    'sheetId': sheet_id,
                    'startRowIndex': 0,
                    'endRowIndex': row_index,
                    'startColumnIndex': 0,
                    'endColumnIndex': 10
                },
                'top': {
                    'style': 'SOLID',
                    'width': 1,
                    'color': {'red': 0.8, 'green': 0.8, 'blue': 0.8}
                },
                'bottom': {
                    'style': 'SOLID',
                    'width': 1,
                    'color': {'red': 0.8, 'green': 0.8, 'blue': 0.8}
                },
                'left': {
                    'style': 'SOLID',
                    'width': 1,
                    'color': {'red': 0.8, 'green': 0.8, 'blue': 0.8}
                },
                'right': {
                    'style': 'SOLID',
                    'width': 1,
                    'color': {'red': 0.8, 'green': 0.8, 'blue': 0.8}
                },
                'innerHorizontal': {
                    'style': 'SOLID',
                    'width': 1,
                    'color': {'red': 0.8, 'green': 0.8, 'blue': 0.8}
                },
                'innerVertical': {
                    'style': 'SOLID',
                    'width': 1,
                    'color': {'red': 0.8, 'green': 0.8, 'blue': 0.8}
                }
            }
        })
        
        # テーブル部分の枠線強調（表の周りだけ少し濃い線）
        for table_section in table_sections:
            start_row = table_section['start']
            end_row = table_section['end']
            cols = table_section['cols']
            
            if start_row < end_row:
                formatting_requests.append({
                    'updateBorders': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': start_row,
                            'endRowIndex': end_row,
                            'startColumnIndex': 0,
                            'endColumnIndex': cols
                        },
                        'top': {
                            'style': 'SOLID',
                            'width': 2,
                            'color': {'red': 0.4, 'green': 0.4, 'blue': 0.7}  # 濃い青色
                        },
                        'bottom': {
                            'style': 'SOLID',
                            'width': 2,
                            'color': {'red': 0.4, 'green': 0.4, 'blue': 0.7}
                        },
                        'left': {
                            'style': 'SOLID',
                            'width': 2,
                            'color': {'red': 0.4, 'green': 0.4, 'blue': 0.7}
                        },
                        'right': {
                            'style': 'SOLID',
                            'width': 2,
                            'color': {'red': 0.4, 'green': 0.4, 'blue': 0.7}
                        }
                    }
                })
            
        
        # データをシートに書き込む処理を追加
        if data:
            # シートにデータを書き込み
            sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"{sheet_name}!A1",
                valueInputOption="USER_ENTERED",
                body={"values": data}
            ).execute()
            logger.info(f"シートにデータを書き込み: {len(data)}行")
        else:
            logger.error("書き込むデータがありません")
        
        # 書式設定の一括適用
        if formatting_requests:
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={'requests': formatting_requests}
            ).execute()
            
            logger.info(f"書式設定を適用: {len(formatting_requests)}件")
        
        # スプレッドシートのURLを生成
        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid={sheet_id}"
        
        # 列幅の調整 - A列は少し狭めに、B列以降は広めに
        column_requests = []
        column_requests.append({
            'updateDimensionProperties': {
                'range': {
                    'sheetId': sheet_id,
                    'dimension': 'COLUMNS',
                    'startIndex': 0,
                    'endIndex': 1
                },
                'properties': {
                    'pixelSize': 200  # A列の幅を200ピクセルに設定
                },
                'fields': 'pixelSize'
            }
        })
        
        # 2列目以降の幅を広めに設定
        column_requests.append({
            'updateDimensionProperties': {
                'range': {
                    'sheetId': sheet_id,
                    'dimension': 'COLUMNS',
                    'startIndex': 1,
                    'endIndex': 10
                },
                'properties': {
                    'pixelSize': 700  # B列以降の幅を700ピクセルに設定
                },
                'fields': 'pixelSize'
            }
        })
        
        # 行の高さを自動調整するための設定
        column_requests.append({
            'autoResizeDimensions': {
                'dimensions': {
                    'sheetId': sheet_id,
                    'dimension': 'ROWS',
                    'startIndex': 0,
                    'endIndex': row_index
                }
            }
        })
        
        # 列幅調整リクエストを実行
        if column_requests:
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={'requests': column_requests}
            ).execute()
        
        return spreadsheet_url, sheet_name
        
    except Exception as e:
        logger.error(f"スプレッドシート作成エラー: {str(e)}")
        traceback.print_exc()
        return None, None

def get_sheet_id(service, spreadsheet_id, sheet_name):
    """シート名からシートIDを取得する"""
    try:
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for sheet in spreadsheet.get('sheets', []):
            if sheet.get('properties', {}).get('title') == sheet_name:
                return sheet.get('properties', {}).get('sheetId')
        return '0'  # デフォルトのシートID
    except Exception as e:
        logger.error(f"シートID取得エラー: {str(e)}")
        return '0'  # エラー時はデフォルトのシートIDを返す

def parse_section_data(section):
    """3C分析セクションのテキストからデータを抽出する"""
    lines = section.strip().split('\n')
    data = {}
    current_key = None
    current_value = []
    
    for line in lines[1:]:  # 最初の行（セクションタイトル）をスキップ
        if line.strip() and ':' in line:
            # 新しいキーが見つかった場合、前のデータを保存
            if current_key:
                data[current_key] = '\n'.join(current_value)
            
            # 新しいキーと値の開始
            key, value = line.split(':', 1)
            current_key = key.strip().lower().replace(' ', '_')
            current_value = [value.strip()]
        elif line.strip():
            # 既存の値に追加
            if current_key:
                current_value.append(line.strip())
    
    # 最後のデータを保存
    if current_key:
        data[current_key] = '\n'.join(current_value)
    
    return data

def parse_impression_share_data(text):
    """テキストからインプレッションシェアデータを解析する"""
    try:
        lines = text.strip().split('\n')
        headers = [h.strip() for h in lines[0].split('\t')]
        
        data = []
        own_data = None
        
        for line in lines[1:]:
            if not line.strip():
                continue
                
            values = [v.strip() for v in line.split('\t')]
            row_data = dict(zip(headers, values))
            
            # インプレッションシェアの値を数値化
            share_value = row_data.get('インプレッション シェア', '0')
            if share_value.startswith('< '):
                # "< 10 %"のような表記を数値化（下限の半分の値とする）
                share_value = float(share_value.replace('< ', '').replace(' %', '')) / 2
            else:
                share_value = float(share_value.replace(' %', ''))
            
            row_data['impression_share_value'] = share_value
            
            # "自分"の行を特定
            if '自分' in row_data.get('表示 URL ドメイン', ''):
                own_data = row_data
            else:
                data.append(row_data)
        
        # インプレッションシェアで降順ソート
        sorted_data = sorted(data, key=lambda x: x.get('impression_share_value', 0), reverse=True)
        
        logger.info(f"インプレッションシェアデータ解析: {len(sorted_data)}件")
        return {
            'own_data': own_data,
            'competitors': sorted_data
        }
    except Exception as e:
        logger.error(f"インプレッションシェアデータ解析エラー: {str(e)}")
        traceback.print_exc()
        return None

@functions_framework.http
def minimal_ai(request):
    """Cloud Functionsのエントリーポイント"""
    # CORSリクエスト用のヘッダーを準備
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        'Access-Control-Max-Age': '3600'
    }
    
    # OPTIONSメソッド（プリフライトリクエスト）への対応
    if request.method == 'OPTIONS':
        return ('', 204, headers)
    
    try:
        # OAuth関連のリクエストを検出
        if request.path == '/slack_oauth_callback' or request.args.get('code'):
            logger.info(f"OAuth関連リクエストを検出: {request.path}")
            return slack_oauth_callback(request)
            
        # リトライリクエストの処理
        retry_num = request.headers.get('X-Slack-Retry-Num')
        if retry_num:
            logger.info(f"リトライリクエストをスキップ: {retry_num}")
            return ({"status": "OK", "message": "Retry skipped"}, 200, headers)
            
        # GET リクエストのヘルスチェック
        if request.method == "GET":
            return ({"status": "OK", "message": "Health check passed"}, 200, headers)
        
        # POSTリクエストのみ処理
        if request.method == "POST":
            request_json = request.get_json(silent=True)
            
            # Slackのチャレンジリクエスト処理
            if request_json and "challenge" in request_json:
                return ({"challenge": request_json["challenge"]}, 200, headers)
            
            # Slackイベントの処理
            if request_json and "event" in request_json:
                result = handle_slack_event(request_json)
                return (result, 200, headers)
            
            # プロパティIDのルートパスのリクエスト処理
            if request_json and "property_id" in request_json:
                logger.info(f"プロパティIDのリクエストを処理します: {request_json.get('property_id')}")
                # ここにプロパティIDに基づく処理を追加
                # 一時的に成功レスポンスを返す
                return ({"status": "OK", "message": "Property ID request received"}, 200, headers)
            
            # その他のPOSTリクエスト（直接のAPIコール等）
            return ({"status": "No event to process"}, 200, headers)
        
        # その他のHTTPメソッドには405を返す
        return ({"status": "Method not allowed"}, 405, headers)
        
    except Exception as e:
        logger.error(f"リクエスト処理エラー: {str(e)}")
        traceback.print_exc()
        return ({"status": "Error", "message": str(e)}, 500, headers)

def analyze_landing_page(url):
    """
    指定されたURLのランディングページを分析し、構造化データを返す
    """
    logger.info(f"LP分析開始: {url}")
    
    # URLの検証と前処理
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    # 余分な文字を削除 - 文字列全体から無効な文字を削除
    url = url.strip()
    url = re.sub(r'[<>"\'\)\]]', '', url)  # 末尾の$を削除して文字列全体から無効文字を削除
    
    # URLが有効なドメイン形式かチェック
    domain_pattern = r'^https?://([a-zA-Z0-9][-a-zA-Z0-9]*\.)+[a-zA-Z0-9][-a-zA-Z0-9]*(/.*)?$'
    if not re.match(domain_pattern, url):
        logger.error(f"無効なURL形式: {url}")
        raise Exception(f"無効なURL形式: {url}")
    
    # Seleniumでページを取得
    if not initialize_selenium():
        raise Exception("Seleniumの初期化に失敗しました")
    
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    
    # Chromeのオプション設定
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled") 
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Dockerで設定された固定パスを使用
    chrome_bin = os.environ.get('CHROME_BIN', '/usr/bin/chromium')
    chromedriver_path = os.environ.get('CHROMEDRIVER_PATH', '/usr/bin/chromedriver')
    
    chrome_options.binary_location = chrome_bin
    
    # WebDriverの設定
    service = Service(executable_path=chromedriver_path)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    try:
        # URLにアクセス（タイムアウト設定付き）
        driver.set_page_load_timeout(30)  # 30秒のタイムアウト
        logger.info(f"アクセス開始: {url}")
        driver.get(url)
        time.sleep(3)  # ページの読み込みを待つ
        
        # ページコンテンツを取得
        page_content = driver.page_source
        title = driver.title
        logger.info(f"ページ取得成功: {title}")
        
        # デバッグのためにスクリーンショット保存
        driver.save_screenshot('/tmp/lp_screenshot.png')
        logger.info("LP分析のスクリーンショット保存: /tmp/lp_screenshot.png")
        
        # HTMLの解析
        soup = BeautifulSoup(page_content, 'html.parser')
        
        # テキストを抽出
        for script in soup(["script", "style"]):
            script.extract()
        
        text = soup.get_text(separator="\n", strip=True)
        
        # メタ情報を取得
        meta_data = {}
        for meta in soup.find_all('meta'):
            name = meta.get('name') or meta.get('property')
            content = meta.get('content')
            if name and content:
                meta_data[name] = content
        
        # AI分析のためのプロンプト作成
        prompt = f"""
以下のウェブサイトのランディングページを徹底的に分析し、「他の競合にはない具体的な特徴」を明確に抽出してください。
曖昧な一般論は避け、具体的な数値、固有の特徴、明確な差別化ポイントを詳細に説明してください。

特に重要なのは「なぜこのLPが選ばれるべきか」という観点での分析です。
例えば、対象ターゲットであれば「幅広い年齢層」のような一般的な記述ではなく、
「35〜45歳の子育て世代で、特に腰痛・肩こりの症状が3年以上続いており、他院で改善しなかった人」のような
具体的な特定ターゲットを抽出してください。

以下の大きなカテゴリを基準に分析を行ってください：

1. コンテンツ構成（具体的な数値や特徴を含めること）
   - メイン訴求：何を最も強く訴えているか（成功率、症例数、独自技術名など具体的に）
   - 提供価値：具体的に何が得られるか（「痛みの軽減」ではなく「施術後3日で痛みが80%軽減」など）
   - CVポイント：具体的なオファー内容（初回割引額、特典内容、期間限定内容など）
   - サービス詳細：提供方法の特徴（所要時間、プロセス、使用機器、特許技術など）
   - 差別化ポイント：他と明確に違う点（「丁寧」ではなく「平均施術時間60分で業界平均の2倍」など）

2. デザインとUX（視覚的特徴を具体的に）
   - 配色：使用されている主要な色とその心理的効果
   - 画像：使用されている画像の種類と効果（実際の施術写真か、ストック写真か）
   - CTA：ボタンの色、位置、表現（具体的な文言）
   - ファーストビュー：画面に最初に表示される要素の詳細

3. ターゲットとマーケティング戦略
   - ターゲット像：年齢層、性別、職業、悩みなど具体的に
   - 訴求方法：どのような言葉・表現で訴えているか具体的な例を挙げる
   - 信頼構築要素：実績数、症例数、メディア掲載、資格など具体的に
   - 価格戦略：価格帯、割引方法、比較対象などを具体的に

特に「他の競合サイトにはない」と思われる要素を明確に特定し、それが「なぜ」差別化になっているのかを
具体的に説明してください。

分析対象ウェブサイト: {url}
タイトル: {title}

メタ情報:
{json.dumps(meta_data, ensure_ascii=False, indent=2)}

ウェブサイトの内容:
{text[:20000]}  # テキストが長すぎる場合は切り詰める

表形式のマークダウンで整形して出力してください。各項目は必ず具体的な内容を含め、曖昧さを排除してください。
「良い」「優れた」などの抽象的な表現は避け、具体的に「何が」「どのように」優れているかを説明してください。
"""
        
        # AIによる分析
        analysis_result = generate_ai_response(prompt)
        
        # 結果を構造化して返す
        return {
            "url": url,
            "title": title,
            "analysis": analysis_result,
            "meta_data": meta_data,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    
    except Exception as e:
        logger.error(f"LP分析エラー: {str(e)}")
        traceback.print_exc()
        # すべてのエラーをキャプチャして再スロー
        raise Exception(f"ページのロードまたは分析に失敗しました: {str(e)}")
    
    finally:
        # ブラウザを閉じる
        try:
            driver.quit()
            logger.info("WebDriverを終了")
        except Exception as e:
            logger.error(f"WebDriver終了エラー: {str(e)}")

def generate_search_keywords(lp_analysis):
    """
    LP分析から検索キーワードを生成する
    """
    prompt = f"""
以下のLP分析を元に、類似のLPを検索するための最適なキーワードを5つ提案してください。
キーワードは検索エンジンで使用することを想定しており、類似のサービスやビジネスを見つけるのに役立つものである必要があります。

LP分析:
{lp_analysis['analysis']}

特にサービスの種類、提供価値、ターゲット層などの重要な要素に注目してください。
キーワードは一般的すぎず、具体的すぎない、検索に最適な長さにしてください。

重要: 「比較」「ランキング」「まとめ」などの比較サイトを検索してしまうようなキーワードは避けてください。
実際の競合サービスや製品そのものを検索するためのキーワードを提案してください。
比較サイトではなく、元のLPと同様の直接的な競合製品・サービスサイトを見つけることが目的です。

回答は以下のような配列形式で提供してください:
["キーワード1", "キーワード2", "キーワード3", "キーワード4", "キーワード5"]
"""
    
    # AIでキーワード生成
    keywords_text = generate_ai_response(prompt)
    
    # 文字列から配列を抽出
    try:
        # 正規表現で配列を抽出
        match = re.search(r'\[.*?\]', keywords_text, re.DOTALL)
        if match:
            keywords_json = match.group(0)
            keywords = json.loads(keywords_json)
            
            # 最大5件に制限
            keywords = keywords[:5]
            return keywords
        else:
            logger.error("キーワード抽出失敗: 配列形式が見つかりません")
            # デフォルトのキーワードを返す
            site_name = lp_analysis.get('title', '').split('|')[0].strip()
            return [site_name, f"{site_name} サービス", f"{site_name} 口コミ"]
    except Exception as e:
        logger.error(f"キーワード抽出エラー: {str(e)}")
        # デフォルトのキーワードを返す
        site_name = lp_analysis.get('title', '').split('|')[0].strip()
        return [site_name, f"{site_name} サービス", f"{site_name} 口コミ"]

def search_lp_reviews(url):
    """
    元のLPのドメインに関する口コミを検索する
    """
    try:
        # URLからドメイン名を抽出
        domain = re.sub(r'^https?://(www\.)?', '', url)
        domain = domain.split('/')[0]  # 最初のパスの前でカット
        
        # 口コミ関連のキーワード
        review_keywords = [
            f"{domain} 口コミ",
            f"{domain} レビュー",
            f"{domain} 評判",
            f"{domain} 感想",
            f"{domain} 体験談"
        ]
        
        logger.info(f"口コミ検索キーワード: {review_keywords}")
        
        # 口コミ検索結果を格納
        review_results = []
        
        # 各キーワードで検索
        for keyword in review_keywords:
            search_results = search_duckduckgo(keyword)
            
            if not search_results:
                continue
                
            # ドメイン自身は除外し、口コミサイトを優先
            for result in search_results:
                result_url = result.get('url')
                result_title = result.get('title', '')
                result_snippet = result.get('description', '')
                
                # 同じドメインは除外（自社サイト内の口コミページは信頼性が低いため）
                if domain in result_url.lower():
                    continue
                    
                # 明らかに口コミ関連のサイトか確認
                if ('口コミ' in result_title or 'レビュー' in result_title or '評判' in result_title or 
                    '口コミ' in result_snippet or 'レビュー' in result_snippet or '評判' in result_snippet):
                    
                    # 口コミサイトの内容を取得
                    try:
                        content = fetch_website_content(result_url)
                        if content and isinstance(content, dict) and content.get('content'):
                            # contentがdict型で、'content'キーが存在する場合のみ処理
                            review_content = str(content['content'])  # 文字列に変換
                            if len(review_content) > 5000:
                                review_content = review_content[0:5000]  # インデックスで直接アクセス
                            
                            review_results.append({
                                'url': result_url,
                                'title': result_title,
                                'snippet': result_snippet,
                                'content': review_content
                            })
                            logger.info(f"口コミ情報取得: {result_title}")
                        
                        # 5件以上取得したら終了
                        if len(review_results) >= 5:
                            break
                    except Exception as e:
                        logger.error(f"口コミサイト取得エラー {result_url}: {str(e)}")
                        continue
            
            # 5件以上取得したら次のキーワードは検索しない
            if len(review_results) >= 5:
                break
                
            # サーバー負荷軽減のため少し待機
            time.sleep(1)
        
        logger.info(f"口コミ検索完了: {len(review_results)}件")
        return review_results
        
    except Exception as e:
        logger.error(f"口コミ検索エラー: {str(e)}")
        return []

def find_similar_landing_pages(original_url, original_analysis, impression_data=None, additional_keywords=None):
    """
    元のLPに類似したランディングページを7つ探す
    """
    similar_lps = []
    existing_domains = []
    
    logger.info(f"類似LP検索開始: {original_url}")
    
    # CSVからのドメイン名(additional_keywords)とインプレッションシェアデータを優先して処理
    csv_domains = []
    if additional_keywords:
        csv_domains = additional_keywords
        logger.info(f"CSVから抽出したドメイン名を優先処理: {csv_domains}")
    
    # インプレッションシェアデータから競合ドメインを取得
    imp_domains = []
    if impression_data and 'competitors' in impression_data:
        for comp in impression_data['competitors']:
            domain = comp.get('表示 URL ドメイン')
            if domain and domain not in imp_domains and domain != "自分":
                imp_domains.append(domain)
        
        logger.info(f"インプレッションシェアデータから抽出されたドメイン: {imp_domains}")
    
    # 通常の検索キーワードを生成
    keywords = []
    try:
        # 分析データからキーワードを生成
        if original_analysis and "analysis" in original_analysis:
            generated_keywords = generate_search_keywords(original_analysis)
            if generated_keywords:
                keywords.extend(generated_keywords)
                logger.info(f"生成された検索キーワード: {keywords}")
    except Exception as e:
        logger.error(f"キーワード生成エラー: {str(e)}")
        # キーワード生成に失敗した場合、元のURLのドメイン名を使用
        from urllib.parse import urlparse
        domain = urlparse(original_url).netloc
        keywords = [domain.replace("www.", "")]
        logger.info(f"キーワード生成失敗、ドメイン名を使用: {keywords}")
    
    # 最初にCSVドメインごとに1つのLPを検索
    for domain in csv_domains:
        if len(similar_lps) >= 7:
            break
            
        try:
            # ドメインで検索実行
            logger.info(f"CSVから抽出したドメイン「{domain}」で検索")
            search_results = search_duckduckgo(domain)
            
            if not search_results:
                logger.warning(f"ドメイン「{domain}」の検索結果なし")
                continue
            
            # 検索結果から該当ドメインのURLを見つける
            found_domain_lp = False
            for result in search_results:
                result_url = result.get("url")
                if not result_url:
                    continue
                
                # URLのドメイン部分を抽出
                from urllib.parse import urlparse
                result_domain = urlparse(result_url).netloc.replace("www.", "")
                
                # 検索したドメインと一致するか確認（www.なしで比較）
                if domain in result_domain or result_domain in domain:
                    # 元のドメインと同じ場合はスキップ
                    original_domain = urlparse(original_url).netloc.replace("www.", "")
                    if result_domain == original_domain:
                        logger.info(f"元のドメインと同じためスキップ: {result_domain}")
                        continue
                    
                    # すでに取得済みのドメインはスキップ
                    if result_domain in existing_domains:
                        logger.info(f"既に取得済みのドメインのためスキップ: {result_domain}")
                        continue
                    
                    existing_domains.append(result_domain)
                    
                    try:
                        # ランディングページを分析
                        logger.info(f"CSVドメインのLP分析: {result_url}")
                        lp_analysis = analyze_landing_page(result_url)
                        
                        # インプレッションシェア情報を追加（存在する場合）
                        if impression_data and 'competitors' in impression_data:
                            for comp in impression_data['competitors']:
                                comp_domain = comp.get('表示 URL ドメイン', '').replace("www.", "")
                                # ドメインが一致するか
                                if comp_domain in result_domain or result_domain in comp_domain:
                                    lp_analysis['impression_share'] = comp.get('インプレッション シェア', 'N/A')
                                    lp_analysis['impression_share_value'] = comp.get('impression_share_value', 0)
                                    break
                        
                        # 検索キーワード情報を追加
                        lp_analysis['search_keyword'] = domain
                        lp_analysis['source'] = "CSV"
                        
                        # 結果に追加
                        similar_lps.append(lp_analysis)
                        logger.info(f"CSV由来のLP追加 ({len(similar_lps)}/7): {result_url}")
                        
                        # このドメインについては1つのLPを見つけたらループを抜ける
                        found_domain_lp = True
                        break
                        
                    except Exception as e:
                        logger.error(f"LP分析エラー {result_url}: {str(e)}")
                        continue
            
            if not found_domain_lp:
                logger.warning(f"ドメイン「{domain}」に一致するLPが見つかりませんでした")
            
        except Exception as e:
            logger.error(f"検索エラー {domain}: {str(e)}")
            continue
    
    # 残りのLPを通常の検索キーワードで補完
    if len(similar_lps) < 7:
        logger.info(f"CSVドメインから{len(similar_lps)}件のLPを取得。残り{7-len(similar_lps)}件を通常検索で補完します。")
        
        # 各キーワードごとに検索
        for keyword in keywords:
            if len(similar_lps) >= 7:
                break
                
            try:
                # 検索実行
                logger.info(f"キーワード「{keyword}」で検索")
                search_results = search_duckduckgo(keyword)
                
                if not search_results:
                    logger.warning(f"キーワード「{keyword}」の検索結果なし")
                    continue
                
                # 検索結果ごとに処理
                for result in search_results[:5]:  # 上位5件のみ処理
                    if len(similar_lps) >= 7:
                        break
                        
                    result_url = result.get("url")
                    if not result_url:
                        continue
                    
                    # URLのドメイン部分を抽出
                    from urllib.parse import urlparse
                    domain = urlparse(result_url).netloc
                    
                    # 元のドメインと同じ場合はスキップ
                    original_domain = urlparse(original_url).netloc
                    if domain == original_domain:
                        logger.info(f"元のドメインと同じためスキップ: {domain}")
                        continue
                    
                    # すでに取得済みのドメインはスキップ
                    if domain in existing_domains:
                        logger.info(f"既に取得済みのドメインのためスキップ: {domain}")
                        continue
                    
                    existing_domains.append(domain)
                    
                    try:
                        # ランディングページを分析
                        logger.info(f"LP分析: {result_url}")
                        lp_analysis = analyze_landing_page(result_url)
                        
                        # インプレッションシェア情報を追加（存在する場合）
                        if impression_data and 'competitors' in impression_data:
                            for comp in impression_data['competitors']:
                                comp_domain = comp.get('表示 URL ドメイン', '')
                                # ドメインが一致するか（www.ありなしも考慮）
                                if (comp_domain == domain or 
                                    comp_domain == domain.replace('www.', '') or 
                                    domain == comp_domain.replace('www.', '')):
                                    
                                    lp_analysis['impression_share'] = comp.get('インプレッション シェア', 'N/A')
                                    lp_analysis['impression_share_value'] = comp.get('impression_share_value', 0)
                                    break
                        
                        # 検索キーワード情報を追加
                        lp_analysis['search_keyword'] = keyword
                        lp_analysis['source'] = "キーワード検索"
                        
                        # 結果に追加
                        similar_lps.append(lp_analysis)
                        logger.info(f"キーワード検索からのLP追加 ({len(similar_lps)}/7): {result_url}")
                        
                    except Exception as e:
                        logger.error(f"LP分析エラー {result_url}: {str(e)}")
                        continue
                
            except Exception as e:
                logger.error(f"検索エラー {keyword}: {str(e)}")
    
    logger.info(f"類似LP検索完了: {len(similar_lps)}件")
    
    # 7個のLP収集後、元のLPの口コミを検索
    logger.info(f"元のLPの口コミ検索を開始: {original_url}")
    review_results = search_lp_reviews(original_url)
    
    # 結果を返す
    return {
        'similar_lps': similar_lps,
        'review_results': review_results,
        'impression_data': impression_data
    }

def generate_lp_analysis_report(original_url, original_analysis, similar_analyses_data):
    """LPの分析レポートを生成する"""
    try:
        # VertexAIが初期化されていない場合は初期化
        if gemini_model is None:
            init_vertexai()
        
        # データの展開
        similar_analyses = similar_analyses_data.get('similar_lps', [])
        review_results = similar_analyses_data.get('review_results', {})  # 空の辞書をデフォルト値に変更
        impression_data = similar_analyses_data.get('impression_data')
        
        # インプレッションシェアの有無に基づく情報を追加
        has_impression_data = impression_data is not None
        
        # 元のLPの表形式レポートを生成するプロンプト
        original_prompt = f"""
以下のランディングページ分析結果を基に、より具体的かつ差別化された特徴を明確にした表形式のレポートを作成してください。

URL: {original_url}
タイトル: {original_analysis['title']}

分析結果:
{original_analysis['analysis']}

このレポートでは、抽象的・一般的な表現を避け、具体的な数値、固有の特徴、明確な差別化ポイントに焦点を当ててください。
例えば「丁寧な対応」ではなく「初回カウンセリング平均50分（業界平均25分）」のように具体的に記述してください。

以下のカテゴリで整理し、各項目は必ず具体的かつ固有の内容にしてください：

1. コンテンツ構成と差別化ポイント
   - メイン訴求の具体的内容（数値、固有技術名など）
   - 提供価値の具体的内容（得られる具体的な結果）
   - CV設計の特徴（オファー内容、導線設計、ボタン配置など）
   - 他社にはない独自の特徴（独自技術、アプローチ、サービス内容など）

2. デザインとUXの特徴
   - 視覚的な差別化要素（配色、画像選択、レイアウトなど）
   - ユーザー体験の工夫（導線、情報提示方法、インタラクションなど）
   - モバイル対応の特徴（レスポンシブ設計、タップ操作の考慮など）

3. ターゲティングと訴求戦略
   - 具体的なターゲット像（年齢、性別、職業、悩み、状況など詳細に）
   - 信頼性担保要素（具体的な実績数、症例数、資格、メディア掲載など）
   - 価格戦略の特徴（具体的な金額、割引方法、比較対象など）
   - 解決できる特定の問題（一般的な「悩み解決」ではなく具体的な症状や状況）

最後に、このLPの全体的な総評を追加してください。以下の点について言及してください：
- このLPの最も効果的な部分とその理由
- 最も改善すべき部分とその具体的な改善案
- このLPが訴求対象に与える全体的な印象
- CVに至るまでの障壁や摩擦ポイント
- このLPを一言で表現するとどうなるか

表形式のマークダウンで整形し、セルは「項目名: 具体的な内容」という形式にしてください。
各項目は必ず一般論ではなく、このLPに固有の具体的な特徴や数値を含めてください。
質問と回答が別々の列に表示されるよう、必ず「項目名:」の形式で記述してください。
"""
        
        # AIでレポート生成
        original_report = generate_ai_response(original_prompt)
        
        # 類似LPの比較レポートを生成するプロンプト
        if similar_analyses:
            # 各類似LPの基本情報を抽出（インプレッションシェア情報も含める）
            similar_lp_info = []
            for idx, lp in enumerate(similar_analyses, 1):
                impression_info = ""
                if 'impression_share' in lp:
                    impression_info = f"\nインプレッションシェア: {lp['impression_share']}"
                    
                    # 他の指標も追加
                    for key, value in lp.items():
                        if key.startswith('impression_data_'):
                            metric_name = key.replace('impression_data_', '')
                            impression_info += f"\n{metric_name}: {value}"
                
                similar_lp_info.append(f"類似LP {idx}: {lp['url']}\nタイトル: {lp['title']}{impression_info}")
            
            similar_lp_text = "\n\n".join(similar_lp_info)
            
            # 比較分析プロンプト - 表形式で横に並べるように指示（インプレッションシェア情報を含む）
            comparison_prompt = f"""
元のLP（{original_url}）と以下の類似LPを比較分析し、7個の類似LPを横に並べた表形式でまとめてください。
抽象的な表現は避け、具体的な数値や特徴、差別化ポイントを明確に示してください。

{"これらの競合LPはインプレッションシェアの高い順（強い順）に並べられています。この情報を分析の参考にしてください。" if has_impression_data else ""}

元のLP分析:
{original_analysis['analysis']}

類似LP:
{similar_lp_text}

以下の観点で比較分析を行い、横に並べた表形式で整理してください：

0. URL（各LPのURLを必ず記載してください）
1. インプレッションシェア（各LPのインプレッションシェアを記載してください。データがない場合は「データなし」と記載）
2. 顧客ターゲットの違い（具体的な属性、ペイン、ゲイン）
3. アピールポイントの差異（独自の強み、USP、訴求方法）
4. 導線設計と体験の違い（CV導線、情報提示順序、CTAの位置や文言）
5. デザイン・レイアウトの特徴（色使い、余白、フォント、視線誘導など）
6. 価格戦略とオファー設計（価格帯、オプション、特典など）
7. 競合LPの良い点と改善点（具体的に）

各類似LPについて、次の点も必ず明記してください：
- 最も優れている点（元のLPと比較して）
- 最も改善すべき点（具体的な問題点と改善案）
- ユーザー体験における摩擦ポイント

表は「比較項目 | 元のLP | 類似LP1 | 類似LP2 | 類似LP3...」のように、
類似LPを横に並べた形式で整理してください。このように類似LPを横方向に7個並べることで、
一目で比較できるようにしてください。

各セルには必ず具体的な特徴を入れてください。
抽象的な表現（「良い」「使いやすい」など）ではなく、
具体的な特徴（「緑基調の配色で安心感を演出」「3ステップのシンプルな申込みフロー」など）を記載してください。

URLとインプレッションシェアは各LPを見分けやすいように必ず表の最初の行に記載してください。
"""
            
            # 比較レポート生成
            comparison_report = generate_ai_response(comparison_prompt)
            
            # 口コミ分析の追加（口コミデータを検索結果から取得）
            # 口コミデータの整形
            reviews_data = ""
            if review_results and isinstance(review_results, dict) and 'original' in review_results:
                # review_resultsが辞書で、originalキーがある場合
                original_reviews = review_results['original']
                for idx, review in enumerate(original_reviews, 1):
                    reviews_data += f"\n口コミ情報 {idx}:\nタイトル: {review['title']}\nURL: {review['url']}\n"
                    reviews_data += f"概要: {review['snippet']}\n"
                    reviews_data += f"内容: {review['content'][:1000]}...\n\n"
            elif review_results and isinstance(review_results, list):
                # review_resultsがリストの場合（従来の形式）
                for idx, review in enumerate(review_results, 1):
                    reviews_data += f"\n口コミ情報 {idx}:\nタイトル: {review['title']}\nURL: {review['url']}\n"
                    reviews_data += f"概要: {review['snippet']}\n"
                    reviews_data += f"内容: {review['content'][:1000]}...\n\n"
            
            reviews_prompt = f"""
元のLP（{original_url}）のユーザー口コミを徹底的に分析してください。
以下は実際にWeb上から収集した口コミ情報です。この情報を基に分析を行ってください。

対象LP: {original_url}

収集した口コミ情報:
{reviews_data if reviews_data else "口コミ情報が見つかりませんでした。その場合は推測ではなく「情報がない」と明記してください。"}

以下の項目について分析してください：

1. 口コミの全体傾向
   - 平均評価（★の数など）
   - 投稿数・件数
   - ポジティブ評価とネガティブ評価の比率

2. ポジティブな口コミで多く言及されている内容（上位5つ）
   - 各項目ごとに具体的な口コミの例を1つ引用してください
   - その項目が評価されている理由を分析してください

3. ネガティブな口コミで多く言及されている内容（上位3つ）
   - 各項目ごとに具体的な口コミの例を1つ引用してください
   - その項目が批判されている理由を分析してください

4. 口コミから見えるユーザーのペインポイント
   - サービス利用前に不安に思っていた点
   - サービス利用後に満足した点
   - 他社サービスから乗り換えた理由

5. 口コミから見える改善点
   - ユーザーが直接指摘している改善点
   - 暗に示唆されている改善可能な点
   - 競合と比較して弱みとなっている点

最後に、口コミ分析から得られる「ユーザーの生の声」を活かした改善提案を3点提示してください。
各提案には具体的な実装方法も含めてください。

分析結果は表形式でまとめ、具体的な口コミ例と数値データを含めて記述してください。
情報が見つからない場合は、「情報なし」と記載してください。

業界・サービスを問わず適用できる汎用的な分析を心がけ、特定業種に偏った表現は避けてください。
"""
            
            # 口コミ分析レポートの生成
            reviews_analysis = generate_ai_response(reviews_prompt)
            
            # 3C分析を生成するプロンプト
            threeC_prompt = f"""
元のランディングページ（{original_url}）と類似LP群の分析結果をもとに、詳細な3C分析（Customer, Competitor, Company）を行ってください。
抽象的・曖昧な表現は避け、具体的で明確な差別化ポイントに焦点を当ててください。

以下は分析済みの情報です:
1. 元のLP分析:
{original_analysis['analysis']}

2. 類似LP情報:
{similar_lp_text}

3. 比較分析:
{comparison_report}

以下の3C分析の質問に、具体的かつ詳細に答えてください。
「〜と思われる」「〜だろう」など推測的な表現は避け、LPから読み取れる確実な情報や具体的な数値に基づいて分析してください。
必ず質問と回答を明確に分けて記述してください。回答はそれぞれ文章から始めて具体的に記述してください。

### 顧客（Customer）
- CVしているメインのユーザー属性は？（性別、年代、家族構成）
- ユーザーのペイン（悩み・問題点）は？具体的な症状や状況は？
- ユーザーのゲイン（得られるメリット）は？具体的な効果や変化は？
- ユーザーのジョブ（達成したいこと）は？
- ユーザーの感情が揺れ動くタイミングと、その時の想定感情は？
- ユーザーが前後1週間でよく調べていると思われる検索ワードは？
- ユーザーから寄せられた口コミで褒められているポイントは？具体的な内容は？
- ユーザーから寄せられた口コミでイマイチだったポイントは？具体的な内容は？

### 競合（Competitor）
- 顕在競合の定義は？どのような企業・サービスが直接的競合となるか？
- 潜在競合の定義は？どのような代替手段・サービスが間接的競合となるか？
- 競合企業の具体的な名前と社数は？
- 競合企業の広告出稿状況は？（媒体、キーワード、リンク先など）
- 競合LPの構成要素の特徴は？
- 競合のアピールポイントは？具体的な表現や数値は？
- 競合のLPでCVしたくなる（心が動く）良い点は？
- 競合のLPでイマイチだと感じる点や心が動かない点は？

### 自社（Company）
- このサービスの明確な強み・USPは？競合にはない独自の特徴は？
- サービス提供者（クライアント）が主張している強みは？
- このサービスの弱みをポジティブに言い換えると？
- ユーザーインタビューやレビューで特に褒められているポイントは？具体的な表現は？
- SNSなどでよく言及されている点は？
- このサービスの最も魅力的な3つのポイントは？具体的に説明してください。
- 競争優位性のある明確な差別化要素は何か？なぜ競合ではなくこのサービスを選ぶべきなのか？

回答形式はすべて「質問: 回答」の形式で記述してください。各回答は必ず具体的で明確な内容を提供してください。
一般的・抽象的な表現は避け、LPから読み取れる具体的な証拠やデータに基づいた分析を行ってください。
回答はスプレッドシートに表示されたとき、質問と回答が別の列に表示されるよう明確に「:」（コロン）で区切って記述してください。
"""
            
            # 3C分析レポート生成
            threeC_report = generate_ai_response(threeC_prompt)
            
            # 競合分析と訴求提案のプロンプト
            competitive_analysis_prompt = f"""
元のランディングページ（{original_url}）と競合LPの分析結果をもとに、競合分析と訴求提案を行います。
インプレッションシェアのデータと今回の競合分析、口コミ分析、3C分析の結果を統合して、
具体的かつ根拠のある提案を行ってください。

以下は分析済みの情報です:
1. 元のLP分析:
{original_analysis['analysis']}

2. 類似LP情報（インプレッションシェア順）:
{similar_lp_text}

3. 比較分析:
{comparison_report}

4. 口コミ分析:
{reviews_analysis}

5. 3C分析:
{threeC_report}

以下の項目について、上記の分析結果をもとに詳細かつ具体的な提案を行ってください:

## 1. 競合の真似すべきところ
競合LPで効果的に機能している要素のうち、自社LPに取り入れるべき点を5つ挙げてください。
各項目について以下を含めてください:
- 競合のどのLPのどの要素が効果的か（具体的なURL、インプレッションシェア、該当箇所）
- なぜその要素が効果的なのか（データや分析結果からの根拠）
- どのように自社LPに取り入れるべきか（具体的な実装方法）

## 2. より差別化を図るべきところ
自社LPですでに優れている点や、さらに差別化すべき点を5つ挙げてください。
各項目について以下を含めてください:
- 現在の自社LPのどの要素が優れているか（具体的な箇所）
- 競合との比較でどのように差別化されているか
- どのようにさらに強化すべきか（具体的な改善方法）

## 3. 自社サービスだけが満たせる訴求
競合が提供できない、自社LPだけが満たせる独自の訴求ポイントを3つ挙げてください。
各項目について以下を含めてください:
- 競合にはない自社の独自価値（具体的に）
- なぜ競合がこれを満たせないのか（技術的・事業的理由）
- どのように訴求すべきか（具体的な表現、配置方法）

## 4. ユーザーからの評価が高い訴求
口コミやユーザー評価から、特に高評価を得ている訴求ポイントを3つ挙げてください。
各項目について以下を含めてください:
- どのような評価を受けているか（具体的な口コミ例）
- なぜユーザーがこの点を評価しているのか
- どのようにLPでさらに強調すべきか

## 5. 有効な訴求仮説
以上の分析を総合し、効果的と思われる訴求仮説を5つ提示してください。
各仮説について以下を必ず含めてください:
- 仮説の内容（具体的な訴求メッセージ）
- 根拠となる分析結果（競合分析、3C分析、口コミ分析から具体的に引用）
- 実装方法（LPのどの位置に、どのように配置するか）
- 期待される効果（具体的なCVR向上の可能性など）

各項目はできるだけ具体的に、データや分析結果に基づいて説明してください。
抽象的な表現や一般論は避け、この分析結果からのみ導き出される固有の提案を行ってください。
特に「有効な訴求仮説」では、競合との比較や自社の強みを具体的に引用し、ユーザーが納得できる
根拠を重視してください。

各セクションはマークダウン形式で整理し、要点が明確に伝わるようにしてください。
"""
            
            # 競合分析と訴求提案の生成
            competitive_analysis = generate_ai_response(competitive_analysis_prompt)
            
            # レポート全体を組み立て（バッククォートを除去）
            raw_report = f"""
# ランディングページ分析レポート

## 1. 元のLP分析
URL: {original_url}

{original_report}

## 2. 類似LP比較分析
{comparison_report}

## 3. ユーザー口コミ分析
{reviews_analysis}

## 4. 3C分析（Customer, Competitor, Company）
{threeC_report}

## 5. 競合分析と訴求提案
{competitive_analysis}
"""
            # Slackに送信用のレポート（バッククォートで囲む）
            full_report = f"```{raw_report}```"
        else:
            # 類似LPがない場合でも3C分析は行う
            threeC_prompt = f"""
ランディングページ（{original_url}）の分析結果をもとに、詳細な3C分析（Customer, Competitor, Company）を行ってください。
抽象的・曖昧な表現は避け、具体的で明確な差別化ポイントに焦点を当ててください。

以下は分析済みの情報です:
LP分析:
{original_analysis['analysis']}

以下の3C分析の質問に、具体的かつ詳細に答えてください。
「〜と思われる」「〜だろう」など推測的な表現は避け、LPから読み取れる確実な情報や具体的な数値に基づいて分析してください。
必ず質問と回答を明確に分けて記述してください。回答はそれぞれ文章から始めて具体的に記述してください。

### 顧客（Customer）
- CVしているメインのユーザー属性は？（性別、年代、家族構成）
- ユーザーのペイン（悩み・問題点）は？具体的な症状や状況は？
- ユーザーのゲイン（得られるメリット）は？具体的な効果や変化は？
- ユーザーのジョブ（達成したいこと）は？
- ユーザーの感情が揺れ動くタイミングと、その時の想定感情は？
- ユーザーが前後1週間でよく調べていると思われる検索ワードは？
- ユーザーから寄せられた口コミで褒められているポイントは？具体的な内容は？
- ユーザーから寄せられた口コミでイマイチだったポイントは？具体的な内容は？

### 競合（Competitor）
- 顕在競合の定義は？どのような企業・サービスが直接的競合となるか？
- 潜在競合の定義は？どのような代替手段・サービスが間接的競合となるか？
- 競合企業の具体的な名前と社数は？
- 競合企業の広告出稿状況は？（媒体、キーワード、リンク先など）
- 競合LPの構成要素の特徴は？
- 競合のアピールポイントは？具体的な表現や数値は？
- 競合のLPでCVしたくなる（心が動く）良い点は？
- 競合のLPでイマイチだと感じる点や心が動かない点は？

### 自社（Company）
- このサービスの明確な強み・USPは？競合にはない独自の特徴は？
- サービス提供者（クライアント）が主張している強みは？
- このサービスの弱みをポジティブに言い換えると？
- ユーザーインタビューやレビューで特に褒められているポイントは？具体的な表現は？
- SNSなどでよく言及されている点は？
- このサービスの最も魅力的な3つのポイントは？具体的に説明してください。
- 競争優位性のある明確な差別化要素は何か？なぜ競合ではなくこのサービスを選ぶべきなのか？

回答形式はすべて「質問: 回答」の形式で記述してください。各回答は必ず具体的で明確な内容を提供してください。
一般的・抽象的な表現は避け、LPから読み取れる具体的な証拠やデータに基づいた分析を行ってください。
回答はスプレッドシートに表示されたとき、質問と回答が別の列に表示されるよう明確に「:」（コロン）で区切って記述してください。
"""
            
            # 3C分析レポート生成
            threeC_report = generate_ai_response(threeC_prompt)
            
            # 訴求提案のプロンプト
            proposal_prompt = f"""
元のランディングページ（{original_url}）の分析結果をもとに、訴求提案を行います。
今回の分析結果から、具体的かつ根拠のある提案を行ってください。

以下は分析済みの情報です:
1. 元のLP分析:
{original_analysis['analysis']}

2. 3C分析:
{threeC_report}

以下の項目について、上記の分析結果をもとに詳細かつ具体的な提案を行ってください:

## 1. 強化すべき訴求ポイント
現在のLPですでに優れている点や、さらに強化すべき点を5つ挙げてください。
各項目について以下を含めてください:
- 現在のLPのどの要素が優れているか（具体的な箇所）
- なぜその要素が効果的なのか（データや分析結果からの根拠）
- どのようにさらに強化すべきか（具体的な改善方法）

## 2. 新しく追加すべき訴求
現在のLPに不足している可能性のある訴求ポイントを5つ挙げてください。
各項目について以下を含めてください:
- どのような訴求が不足しているか（具体的に）
- なぜその訴求が必要か（ターゲットのニーズや競合状況からの根拠）
- どのように実装すべきか（具体的な表現、配置方法）

## 3. 差別化すべき訴求
潜在的な競合との差別化を図るための訴求ポイントを3つ挙げてください。
各項目について以下を含めてください:
- 競合よりも優れている可能性がある要素（具体的に）
- なぜこの要素が差別化ポイントになるか
- どのように訴求すべきか（具体的な表現方法）

## 4. ユーザーの悩みに応える訴求
ターゲットユーザーの具体的な悩みや課題に直接応える訴求を3つ挙げてください。
各項目について以下を含めてください:
- ユーザーのどのような悩みに応えるか（具体的な悩み）
- どのように解決できるか（具体的な解決方法）
- どのように訴求すべきか（効果的な表現方法）

## 5. 有効な訴求仮説
以上の分析を総合し、効果的と思われる訴求仮説を5つ提示してください。
各仮説について以下を必ず含めてください:
- 仮説の内容（具体的な訴求メッセージ）
- 根拠となる分析結果（3C分析から具体的に引用）
- 実装方法（LPのどの位置に、どのように配置するか）
- 期待される効果（具体的なCVR向上の可能性など）

各項目はできるだけ具体的に、データや分析結果に基づいて説明してください。
抽象的な表現や一般論は避け、この分析結果からのみ導き出される固有の提案を行ってください。
特に「有効な訴求仮説」では、ターゲットのニーズと自社の強みを具体的に引用し、ユーザーが納得できる
根拠を重視してください。

各セクションはマークダウン形式で整理し、要点が明確に伝わるようにしてください。
"""
            
            # 訴求提案の生成
            proposal_analysis = generate_ai_response(proposal_prompt)
            
            # レポート全体を組み立て（バッククォートを除去）
            raw_report = f"""
# ランディングページ分析レポート

## 1. 元のLP分析
URL: {original_url}

{original_report}

## 2. 3C分析（Customer, Competitor, Company）
{threeC_report}

## 3. 訴求提案
{proposal_analysis}
"""
            # Slackに送信用のレポート（バッククォートで囲む）
            full_report = f"```{raw_report}```"
        
        # 3C分析データを構造化
        threeC_data = {
            'customer': {
                'main_target': '',  # メインターゲット
                'pain_points': '',  # ペインポイント
                'gains': '',        # ゲイン
                'jobs': '',         # ジョブ
                'emotions': '',     # 感情変化
                'related_keywords': '', # 関連キーワード
                'positive_reviews': '', # 口コミ評価（良い点）
                'negative_reviews': ''  # 口コミ評価（改善点）
            },
            'competitor': {
                'direct_competitors_def': '', # 顕在競合の定義
                'indirect_competitors_def': '', # 潜在競合の定義
                'competitor_list': '', # 競合企業の具体的な名前と社数
                'ad_strategy': '',      # 広告戦略
                'lp_features': '',      # LP特徴
                'appeal_points': '',    # 競合のアピールポイント
                'strengths': '',        # 競合LPの良い点
                'weaknesses': ''        # 競合LPの改善点
            },
            'company': {
                'usp': '',              # USP
                'claimed_strengths': '', # 主張している強み
                'actual_strengths': '',  # 実際の強み
                'improvements': '',      # 改善点
                'differentiation': '',    # 差別化要素
                'user_reviews': '',      # ユーザーからの評価ポイント
                'sns_mentions': '',      # SNSでの言及内容
                'top_three_points': ''    # 最も魅力的な3つのポイント
            }
        }
        
        # 3C分析の結果をパース
        sections = threeC_report.split('###')
        for section in sections:
            if '顧客（Customer）' in section:
                threeC_data['customer'] = parse_section_data(section)
            elif '競合（Competitor）' in section:
                threeC_data['competitor'] = parse_section_data(section)
            elif '自社（Company）' in section:
                threeC_data['company'] = parse_section_data(section)
        
        # スプレッドシートの作成（完全なレポートを渡す）
        spreadsheet_url, sheet_name = create_3c_analysis_spreadsheet(original_url, raw_report)
        
        if spreadsheet_url:
            # スプレッドシートURLのみを返す
            return f"\n\nLP分析が完了しました！\n{spreadsheet_url}"
        else:
            # スプレッドシート作成に失敗した場合はエラーメッセージを返す
            return "スプレッドシート作成に失敗しました。"
        
    except Exception as e:
        logger.error(f"レポート生成エラー: {str(e)}")
        traceback.print_exc()
        return f"レポート生成中にエラーが発生しました。エラー詳細: {str(e)}"

@functions_framework.http
def slack_oauth_callback(request):
    """SlackのOAuthコールバック処理を行うエンドポイント"""
    # 完全に単純化したバージョン
    try:
        # まずすべての情報をログに出力
        logger.info(f"リクエスト受信: path={request.path}, args={dict(request.args)}")
        
        # codeパラメータを確認
        code = request.args.get('code')
        if not code:
            logger.error("codeパラメータがありません")
            return jsonify({"message": "Code parameter missing", "status": "ERROR"}), 400
            
        # 環境変数を確認
        client_id = os.environ.get('SLACK_CLIENT_ID')
        client_secret = os.environ.get('SLACK_CLIENT_SECRET')
        
        logger.info(f"環境変数: client_id={client_id[:5]}..., client_secret={client_secret[:5]}...")
        
        if not client_id or not client_secret:
            logger.error(f"環境変数不足: client_id={bool(client_id)}, client_secret={bool(client_secret)}")
            return jsonify({"message": "Credentials missing", "status": "ERROR"}), 500
            
        # Slackにリクエスト
        response = requests.post(
            'https://slack.com/api/oauth.v2.access',
            data={
                'code': code,
                'client_id': client_id,
                'client_secret': client_secret
            }
        )
        
        # 応答を確認
        result = response.json()
        logger.info(f"Slack応答: {result}")
        
        if not result.get('ok'):
            logger.error(f"Slack OAuth Error: {result}")
            return jsonify({"message": f"Slack error: {result.get('error')}", "status": "ERROR"}), 400
            
        # 成功 - トークンを保存
        bot_token = result.get('access_token')
        os.environ["SLACK_BOT_TOKEN"] = bot_token
        
        # 成功画面
        html_response = """
        <html><body>
        <h1>インストール成功！</h1>
        <p>Slackボットがインストールされました。</p>
        </body></html>
        """
        
        return html_response
        
    except Exception as e:
        # すべての例外を詳細にログ出力
        logger.error(f"予期せぬエラー: {str(e)}")
        traceback.print_exc()
        return jsonify({"message": f"Error: {str(e)}", "status": "ERROR"}), 500

def analyze_csv_data(text):
    """タブ区切りテキストデータからドメインと数値を抽出"""
    # デバッグログを追加
    logger.info(f"入力データ（先頭500文字）: {text[:500]}")
    
    # HTMLエンコードを元に戻す
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    
    # Slackの特殊なURL表記を処理 <http://example.com|example.com> → example.com
    text = re.sub(r"<http[^|]+\|([^>]+)>", r"\1", text)
    
    # タイムスタンプを削除 (例: 2025-04-10 13:03:26.020 JST)
    text = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+ [A-Z]{3,4}", "", text)
    
    # 通常のURLを除外
    url_pattern = r"https?://[^\s<>]+"
    text = re.sub(url_pattern, "", text)
    
    # /dataコマンドを除外
    text = text.replace("/data", "")
    
    # 処理後のテキストをログ出力
    logger.info(f"前処理後テキスト（先頭500文字）: {text[:500]}")
    
    # 直接ドメインとシェアを抽出
    top_domains = extract_domains_and_shares(text)
    
    # JSON形式で結果を返す
    if top_domains:
        result = json.dumps([f"{domain}: {share}" for domain, _, share in top_domains])
        return result
    else:
        return json.dumps(["エラー: 有効なドメインデータが見つかりませんでした"])

def extract_domains_and_shares(text):
    """テキストからドメインとシェア情報を直接抽出する"""
    try:
        # ドメインパターン (複数のTLDをサポート)
        domain_pattern = r'([a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|[a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,}|自分)'
        
        # パーセント値パターン (数値+%記号 または "< 10 %"形式)
        percent_pattern = r'(\d+\.?\d*%|\d+\.?\d*\s*%|< 10 %|<\s*10\s*%)'
        
        # ドメインとパーセント値のリスト
        domain_percent_pairs = []
        
        # アプローチ1: 同じ行内でドメインとシェアを探す
        lines = text.split('\n')
        
        for line in lines:
            domain_matches = re.finditer(domain_pattern, line)
            for domain_match in domain_matches:
                domain = domain_match.group(1)
                # ドメインの後方でパーセント値を探す
                start_pos = domain_match.end()
                rest_of_line = line[start_pos:]
                
                # パーセント値を探す
                percent_matches = re.finditer(percent_pattern, rest_of_line)
                for percent_match in percent_matches:
                    share = percent_match.group(1).strip()
                    
                    # < 10% を除外
                    if "< 10" not in share and "<10" not in share:
                        # 数値部分を取得
                        share_value = re.sub(r'[^0-9.]', '', share)
                        if share_value:
                            try:
                                float_value = float(share_value)
                                # 同じドメインが既に追加されていないか確認
                                if not any(d == domain for d, _, _ in domain_percent_pairs):
                                    domain_percent_pairs.append((domain, float_value, share))
                                    # 一度シェアを見つけたら次のドメインへ
                                    break
                            except ValueError:
                                continue
        
        # アプローチ2: スペースで区切られたトークン単位の検索
        if not domain_percent_pairs:
            logger.info("行単位の検索で結果が見つからなかったため、トークン単位で検索します")
            words = re.split(r'\s+', text)
            
            for i, word in enumerate(words):
                domain_match = re.match(domain_pattern, word)
                if domain_match and i + 1 < len(words):
                    domain = domain_match.group(1)
                    # ドメインの次のトークンをシェアと仮定
                    next_word = words[i + 1]
                    percent_match = re.match(percent_pattern, next_word)
                    
                    if percent_match:
                        share = percent_match.group(1).strip()
                        if "< 10" not in share and "<10" not in share:
                            share_value = re.sub(r'[^0-9.]', '', share)
                            if share_value:
                                try:
                                    float_value = float(share_value)
                                    if not any(d == domain for d, _, _ in domain_percent_pairs):
                                        domain_percent_pairs.append((domain, float_value, share))
                                except ValueError:
                                    continue
        
        # アプローチ3: 正規表現でドメインとそれに続く数値を一括検索
        if not domain_percent_pairs:
            logger.info("トークン単位の検索でも結果が見つからなかったため、パターンマッチで検索します")
            combined_pattern = f'({domain_pattern})[^0-9]*?(\\d+\\.?\\d*)[\s%]*'
            combined_matches = re.findall(combined_pattern, text)
            
            for domain, share_value in combined_matches:
                if share_value:
                    try:
                        float_value = float(share_value)
                        if not any(d == domain for d, _, _ in domain_percent_pairs):
                            domain_percent_pairs.append((domain, float_value, f"{share_value}%"))
                    except ValueError:
                        continue
        
        # 結果がなければ最後の手段としてURLとパーセント値を探す
        if not domain_percent_pairs:
            logger.info("最後の手段として全テキストからドメインとパーセント値を抽出します")
            all_domains = re.findall(domain_pattern, text)
            all_percents = re.findall(r'(\\d+\\.?\\d*)\\s*%', text)
            
            # パーセント値がありそうなものだけ抽出
            valid_percents = []
            for p in all_percents:
                try:
                    float_value = float(p)
                    if float_value >= 10:  # 10%以上のみ
                        valid_percents.append((float_value, f"{p}%"))
                except ValueError:
                    continue
            
            # ドメインとパーセント値の数が近ければ組み合わせる
            if len(all_domains) > 0 and len(valid_percents) > 0 and abs(len(all_domains) - len(valid_percents)) < 5:
                # パーセント値で降順ソート
                valid_percents.sort(reverse=True)
                
                # 上位のドメインを選ぶ (最大7つ)
                for i, domain in enumerate(all_domains):
                    if i < len(valid_percents) and i < 7:
                        float_value, share = valid_percents[i]
                        domain_percent_pairs.append((domain, float_value, share))
        
        # 結果を降順ソート
        if domain_percent_pairs:
            # 10%未満を再度フィルタリング（確実に除外）
            filtered_pairs = []
            for domain, value, share in domain_percent_pairs:
                if value >= 10 and "< 10" not in share and "<10" not in share:
                    filtered_pairs.append((domain, value, share))
            
            # 結果を降順ソート
            filtered_pairs.sort(key=lambda x: x[1], reverse=True)
            
            # 上位7件を返す
            top_domains = filtered_pairs[:7]
            logger.info(f"抽出されたドメインとシェア: {top_domains}")
            return top_domains
        
        logger.error("ドメインとシェア値のペアが見つかりませんでした")
        return []
    except Exception as e:
        logger.error(f"ドメイン抽出エラー: {str(e)}")
        traceback.print_exc()
        return []

def find_similar_landing_pages_with_domains(top_domains):
    """ドメインリストを使用して類似ランディングページを検索する"""
    similar_lps = []
    existing_domains = []
    
    logger.info(f"類似LP検索開始: {top_domains}")
    
    # 空のリストの場合はエラーメッセージを返す
    if not top_domains:
        return {
            'similar_lps': [],
            'review_results': {},
            'error': "インプレッションシェアデータからドメインを抽出できませんでした。"
        }
    
    for domain, share in top_domains:
        try:
            # すでに取得済みドメインをスキップ
            if domain in existing_domains:
                continue
                
            existing_domains.append(domain)
            
            # 「自分」ドメインの特別処理 - スキップする
            if domain == "自分":
                logger.info("「自分」ドメインはスキップします")
                continue
            
            
            # ドメインをDuckDuckGoで検索して正確なURLを取得
            logger.info(f"ドメイン「{domain}」を検索しています...")
            search_results = search_duckduckgo(domain)
            
            # 該当ドメインのURLを検索結果から見つける
            target_url = None
            if search_results:
                for result in search_results:
                    result_url = result.get("url", "")
                    # 検索結果のURLからドメイン部分を抽出
                    parsed_url = urlparse(result_url)
                    result_domain = parsed_url.netloc.replace("www.", "")
                    
                    # 検索結果のドメインが元のドメインを含むか確認
                    if domain in result_domain or result_domain in domain:
                        target_url = result_url
                        logger.info(f"ドメイン「{domain}」の検索結果URL: {target_url}")
                        break
            
            # 該当URLが見つからなかった場合は、直接URLを構築（フォールバック）
            if not target_url:
                logger.warning(f"ドメイン「{domain}」の検索結果が見つかりませんでした。直接URLを構築します。")
                # ドメインがhttpで始まっていない場合のみ、httpsを追加
                if not domain.startswith(('http://', 'https://')):
                    target_url = f'https://{domain}'
                else:
                    target_url = domain
            
            # LP分析を実行
            logger.info(f"LP分析を開始: {target_url}")
            lp_analysis = analyze_landing_page(target_url)
            
            # インプレッションシェアデータを追加
            lp_analysis['impression_share'] = f"{share}%"
            lp_analysis['impression_share_value'] = float(share.replace(',', '.'))
            lp_analysis['original_domain'] = domain  # 元のドメイン名も保存
            
            # 分析結果を追加
            similar_lps.append(lp_analysis)
            logger.info(f"競合LP追加 ({len(similar_lps)}/{min(7, len(top_domains))}): {target_url} (インプレッションシェア: {share}%)")
            
            # 7件溜まったら終了
            if len(similar_lps) >= 7:
                break
                
            # サーバー負荷軽減のため少し待機
            time.sleep(2)
        except Exception as e:
            logger.error(f"競合LP分析エラー {domain}: {str(e)}")
            continue
    
    # 結果をまとめて返す
    return {
        'similar_lps': similar_lps,
        'review_results': {},
        'impression_data': {
            'competitors': [
                {
                    '表示 URL ドメイン': domain,
                    'インプレッション シェア': f"{share}%",
                    'impression_share_value': float(share.replace(',', '.'))
                } for domain, share in top_domains if domain != "自分"  # 「自分」は除外
            ] if top_domains else []
        }
    }

def find_similar_landing_pages_with_ai_filtering(original_url, original_analysis, impression_data=None, additional_keywords=None):
    """
    AIを活用して比較サイトを除外した類似LPを検出する拡張機能
    """
    try:
        similar_lps = []
        existing_domains = []
        
        logger.info(f"AI選別による類似LP検索開始: {original_url}")
        
        # ステップ1: スクレイピング結果収集
        search_results_collection = []
        
        # CSVからのドメイン名(additional_keywords)とインプレッションシェアデータを優先して処理
        csv_domains = []
        if additional_keywords:
            csv_domains = additional_keywords
            logger.info(f"CSVから抽出したドメイン名: {csv_domains}")
        
        # インプレッションシェアデータから競合ドメインを取得
        imp_domains = []
        if impression_data and 'competitors' in impression_data:
            for comp in impression_data['competitors']:
                domain = comp.get('表示 URL ドメイン')
                if domain and domain not in imp_domains and domain != "自分":
                    imp_domains.append(domain)
            
            logger.info(f"インプレッションシェアデータから抽出されたドメイン: {imp_domains}")
        
        # 検索キーワードを集める配列
        all_search_terms = []
        
        # CSVドメインを検索キーワードとして追加
        all_search_terms.extend(csv_domains)
        
        # インプレッションシェアドメインを追加
        all_search_terms.extend(imp_domains)
        
        # 通常の検索キーワードを生成
        try:
            # 分析データからキーワードを生成
            if original_analysis and "analysis" in original_analysis:
                generated_keywords = generate_search_keywords(original_analysis)
                if generated_keywords:
                    all_search_terms.extend(generated_keywords)
                    logger.info(f"AIが生成した検索キーワード: {generated_keywords}")
        except Exception as e:
            logger.error(f"キーワード生成エラー: {str(e)}")
            # キーワード生成に失敗した場合、元のURLのドメイン名を使用
            from urllib.parse import urlparse
            domain = urlparse(original_url).netloc
            all_search_terms.append(domain.replace("www.", ""))
            logger.info(f"キーワード生成失敗、ドメイン名を使用: {domain}")
        
        # 重複の削除
        unique_search_terms = list(dict.fromkeys(all_search_terms))
        logger.info(f"収集した検索キーワード（合計{len(unique_search_terms)}件）: {unique_search_terms}")
        
        # ステップ2: 各キーワードで検索結果を収集
        for keyword in unique_search_terms[:10]:  # 最大10個のキーワードまで処理
            try:
                results = search_duckduckgo(keyword)
                if results:
                    # 検索結果を保存
                    search_results_collection.append({
                        "keyword": keyword,
                        "results": results[:10]  # 上位10件のみ
                    })
                    logger.info(f"「{keyword}」の検索結果{len(results)}件を収集")
                    time.sleep(1)  # 負荷軽減
            except Exception as e:
                logger.error(f"検索エラー {keyword}: {str(e)}")
                continue
        
        # 十分な検索結果が得られなかった場合は従来の方法にフォールバック
        if len(search_results_collection) == 0:
            logger.warning("検索結果が得られませんでした。従来の方法を使用します。")
            return find_similar_landing_pages_original(original_url, original_analysis, impression_data, additional_keywords)
        
        # ステップ3: AIに最適な競合サービス名を選んでもらう
        prompt = f"""
あなたはマーケティング専門家です。以下の検索結果から、指定されたURLの直接的な競合となる7つのサービス名を特定してください。

元のサービスURL: {original_url}
元のサービス分析:
{json.dumps(original_analysis, ensure_ascii=False, default=str)[:2000]}

検索結果データ:
{json.dumps(search_results_collection, ensure_ascii=False, default=str)[:5000]}

重要:
- 「比較」「ランキング」「まとめ」などの比較サイトは除外してください
- 実際のサービス/製品を提供している直接競合のみを選んでください
- サービス名を具体的に挙げてください（「〇〇クリニック」「△△スクール」など）
- 元のサービスと同じ分野・業界の競合を選んでください
- できるだけ認知度が高く、代表的なサービスを優先してください

以下の形式で7つのサービス名を出力してください:
["サービス名1", "サービス名2", "サービス名3", "サービス名4", "サービス名5", "サービス名6", "サービス名7"]

JSONのみを出力し、余分な説明は不要です。
"""
        # AIで競合サービス名を抽出
        try:
            service_names_json = generate_ai_response(prompt)
            
            # JSONの抽出（正規表現で配列を取得）
            service_names_match = re.search(r'\[.*?\]', service_names_json, re.DOTALL)
            if not service_names_match:
                logger.error("AIからのサービス名抽出に失敗しました")
                # 既存の方法にフォールバック
                return find_similar_landing_pages_original(original_url, original_analysis, impression_data, additional_keywords)
            
            service_names = json.loads(service_names_match.group(0))
            logger.info(f"AIが選んだ競合サービス名: {service_names}")
        except Exception as e:
            logger.error(f"AIによる競合サービス名選別でエラー: {str(e)}")
            # 既存の方法にフォールバック
            return find_similar_landing_pages_original(original_url, original_analysis, impression_data, additional_keywords)
        
        # ステップ4: 各サービス名で検索して最適なURLを取得
        for service_name in service_names:
            if len(similar_lps) >= 7:
                break
                
            try:
                # サービス名で検索
                service_results = search_duckduckgo(service_name)
                if not service_results:
                    logger.warning(f"サービス「{service_name}」の検索結果がありません")
                    continue
                
                # 検索結果から最適なURLを選ぶようAIに依頼
                url_selection_prompt = f"""
サービス「{service_name}」の公式サイトまたは最も関連性の高いURLを以下の検索結果から1つだけ選んでください。
比較サイトやレビューサイトではなく、サービス提供元の公式サイトを優先してください。

検索結果:
{json.dumps(service_results[:10], ensure_ascii=False, default=str)}

URLのみを返してください（余計な文字は含めないでください）:
"""
                try:
                    best_url_response = generate_ai_response(url_selection_prompt)
                    best_url = best_url_response.strip()
                    
                    # URLの検証
                    if not best_url.startswith(('http://', 'https://')):
                        logger.warning(f"AIから返されたURL '{best_url}' が不正なため、先頭の検索結果を使用します")
                        best_url = service_results[0].get("url")
                except Exception as e:
                    logger.error(f"AIによるURL選択でエラー: {str(e)}")
                    # AIが失敗した場合は先頭の検索結果を使用
                    best_url = service_results[0].get("url")
                
                # ドメイン確認（重複排除）
                from urllib.parse import urlparse
                domain = urlparse(best_url).netloc.replace("www.", "")
                original_domain = urlparse(original_url).netloc.replace("www.", "")
                
                # 元のドメインと同じ場合はスキップ
                if domain == original_domain:
                    logger.info(f"元のドメインと同じためスキップ: {domain}")
                    continue
                
                # すでに取得済みのドメインはスキップ
                if domain in existing_domains:
                    logger.info(f"既に取得済みのドメインのためスキップ: {domain}")
                    continue
                
                existing_domains.append(domain)
                
                # LP分析実行
                lp_analysis = analyze_landing_page(best_url)
                lp_analysis['service_name'] = service_name  # サービス名を追加
                
                # インプレッションシェア情報を追加（存在する場合）
                if impression_data and 'competitors' in impression_data:
                    for comp in impression_data['competitors']:
                        comp_domain = comp.get('表示 URL ドメイン', '').replace("www.", "")
                        # ドメインが一致するか
                        if comp_domain in domain or domain in comp_domain:
                            lp_analysis['impression_share'] = comp.get('インプレッション シェア', 'N/A')
                            lp_analysis['impression_share_value'] = comp.get('impression_share_value', 0)
                            break
                
                # 検索キーワード情報を追加
                lp_analysis['search_keyword'] = service_name
                lp_analysis['source'] = "AI選別"
                
                # 結果に追加
                similar_lps.append(lp_analysis)
                logger.info(f"AI選別による競合LP追加 ({len(similar_lps)}/7): {best_url} (サービス名: {service_name})")
                
                time.sleep(1)  # サーバー負荷軽減
                
            except Exception as e:
                logger.error(f"サービス「{service_name}」の検索・分析エラー: {str(e)}")
                continue
        
        # 十分な結果が得られなかった場合は既存メソッドで補完
        if len(similar_lps) < 3:
            logger.warning("AIによる競合検出が不十分なため従来のメソッドで補完します")
            original_results = find_similar_landing_pages_original(
                original_url, original_analysis, impression_data, additional_keywords)
            
            # 結果が配列ではなく辞書の場合の処理
            remaining_results = []
            if isinstance(original_results, dict) and 'similar_lps' in original_results:
                remaining_results = original_results['similar_lps']
            else:
                remaining_results = original_results
            
            # 重複を避けながら追加
            remaining_slots = 7 - len(similar_lps)
            added = 0
            
            for result in remaining_results:
                if added >= remaining_slots:
                    break
                
                result_url = result.get("url", "")
                result_domain = urlparse(result_url).netloc.replace("www.", "")
                
                if result_domain not in existing_domains:
                    similar_lps.append(result)
                    existing_domains.append(result_domain)
                    added += 1
        
        logger.info(f"AI選別による競合LP検出完了: {len(similar_lps)}件")
        
        # 口コミ検索を実行
        review_results = {}
        try:
            if original_url:
                logger.info(f"元サイト「{original_url}」の口コミ検索を実行")
                reviews = search_lp_reviews(original_url)
                if reviews:
                    review_results['original'] = reviews
                    logger.info(f"元サイトの口コミ{len(reviews)}件を取得")
        except Exception as e:
            logger.error(f"口コミ検索エラー: {str(e)}")
        
        # 結果をまとめて返す
        return {
            'similar_lps': similar_lps,
            'review_results': review_results,
            'impression_data': impression_data,
            'source': 'ai_filtering'
        }
    except Exception as e:
        logger.error(f"AI選別による競合LP検出エラー: {str(e)}")
        traceback.print_exc()
        # エラー時は元の関数にフォールバック
        return find_similar_landing_pages_original(original_url, original_analysis, impression_data, additional_keywords)

# 元の関数をリネーム
def find_similar_landing_pages_original(original_url, original_analysis, impression_data=None, additional_keywords=None):
    """元の実装の類似LP検索（変更なし）"""
    similar_lps = []
    existing_domains = []
    
    logger.info(f"類似LP検索開始: {original_url}")
    
    # CSVからのドメイン名(additional_keywords)とインプレッションシェアデータを優先して処理
    csv_domains = []
    if additional_keywords:
        csv_domains = additional_keywords
        logger.info(f"CSVから抽出したドメイン名を優先処理: {csv_domains}")
    
    # インプレッションシェアデータから競合ドメインを取得
    imp_domains = []
    if impression_data and 'competitors' in impression_data:
        for comp in impression_data['competitors']:
            domain = comp.get('表示 URL ドメイン')
            if domain and domain not in imp_domains and domain != "自分":
                imp_domains.append(domain)
        
        logger.info(f"インプレッションシェアデータから抽出されたドメイン: {imp_domains}")
    
    # 通常の検索キーワードを生成
    keywords = []
    try:
        # 分析データからキーワードを生成
        if original_analysis and "analysis" in original_analysis:
            generated_keywords = generate_search_keywords(original_analysis)
            if generated_keywords:
                keywords.extend(generated_keywords)
                logger.info(f"生成された検索キーワード: {keywords}")
    except Exception as e:
        logger.error(f"キーワード生成エラー: {str(e)}")
        # キーワード生成に失敗した場合、元のURLのドメイン名を使用
        from urllib.parse import urlparse
        domain = urlparse(original_url).netloc
        keywords = [domain.replace("www.", "")]
        logger.info(f"キーワード生成失敗、ドメイン名を使用: {keywords}")
    
    # 最初にCSVドメインごとに1つのLPを検索
    for domain in csv_domains:
        if len(similar_lps) >= 7:
            break
            
        try:
            # ドメインで検索実行
            logger.info(f"CSVから抽出したドメイン「{domain}」で検索")
            search_results = search_duckduckgo(domain)
            
            if not search_results:
                logger.warning(f"ドメイン「{domain}」の検索結果なし")
                continue
            
            # 検索結果から該当ドメインのURLを見つける
            found_domain_lp = False
            for result in search_results:
                result_url = result.get("url")
                if not result_url:
                    continue
                
                # URLのドメイン部分を抽出
                from urllib.parse import urlparse
                result_domain = urlparse(result_url).netloc.replace("www.", "")
                
                # 検索したドメインと一致するか確認（www.なしで比較）
                if domain in result_domain or result_domain in domain:
                    # 元のドメインと同じ場合はスキップ
                    original_domain = urlparse(original_url).netloc.replace("www.", "")
                    if result_domain == original_domain:
                        logger.info(f"元のドメインと同じためスキップ: {result_domain}")
                        continue
                    
                    # すでに取得済みのドメインはスキップ
                    if result_domain in existing_domains:
                        logger.info(f"既に取得済みのドメインのためスキップ: {result_domain}")
                        continue
                    
                    existing_domains.append(result_domain)
                    
                    try:
                        # ランディングページを分析
                        logger.info(f"CSVドメインのLP分析: {result_url}")
                        lp_analysis = analyze_landing_page(result_url)
                        
                        # インプレッションシェア情報を追加（存在する場合）
                        if impression_data and 'competitors' in impression_data:
                            for comp in impression_data['competitors']:
                                comp_domain = comp.get('表示 URL ドメイン', '').replace("www.", "")
                                # ドメインが一致するか
                                if comp_domain in result_domain or result_domain in comp_domain:
                                    lp_analysis['impression_share'] = comp.get('インプレッション シェア', 'N/A')
                                    lp_analysis['impression_share_value'] = comp.get('impression_share_value', 0)
                                    break
                        
                        # 検索キーワード情報を追加
                        lp_analysis['search_keyword'] = domain
                        lp_analysis['source'] = "CSV"
                        
                        # 結果に追加
                        similar_lps.append(lp_analysis)
                        logger.info(f"CSV由来のLP追加 ({len(similar_lps)}/7): {result_url}")
                        
                        # このドメインについては1つのLPを見つけたらループを抜ける
                        found_domain_lp = True
                        break
                        
                    except Exception as e:
                        logger.error(f"LP分析エラー {result_url}: {str(e)}")
                        continue
            
            if not found_domain_lp:
                logger.warning(f"ドメイン「{domain}」に一致するLPが見つかりませんでした")
            
        except Exception as e:
            logger.error(f"検索エラー {domain}: {str(e)}")
            continue
    
    # 残りのLPを通常の検索キーワードで補完
    if len(similar_lps) < 7:
        logger.info(f"CSVドメインから{len(similar_lps)}件のLPを取得。残り{7-len(similar_lps)}件を通常検索で補完します。")
        
        # 各キーワードごとに検索
        for keyword in keywords:
            if len(similar_lps) >= 7:
                break
                
            try:
                # 検索実行
                logger.info(f"キーワード「{keyword}」で検索")
                search_results = search_duckduckgo(keyword)
                
                if not search_results:
                    logger.warning(f"キーワード「{keyword}」の検索結果なし")
                    continue
                
                # 検索結果ごとに処理
                for result in search_results[:5]:  # 上位5件のみ処理
                    if len(similar_lps) >= 7:
                        break
                        
                    result_url = result.get("url")
                    if not result_url:
                        continue
                    
                    # URLのドメイン部分を抽出
                    from urllib.parse import urlparse
                    domain = urlparse(result_url).netloc
                    
                    # 元のドメインと同じ場合はスキップ
                    original_domain = urlparse(original_url).netloc
                    if domain == original_domain:
                        logger.info(f"元のドメインと同じためスキップ: {domain}")
                        continue
                    
                    # すでに取得済みのドメインはスキップ
                    if domain in existing_domains:
                        logger.info(f"既に取得済みのドメインのためスキップ: {domain}")
                        continue
                    
                    existing_domains.append(domain)
                    
                    try:
                        # ランディングページを分析
                        logger.info(f"検索キーワード「{keyword}」からのLP分析: {result_url}")
                        lp_analysis = analyze_landing_page(result_url)
                        
                        # インプレッションシェア情報を追加（存在する場合）
                        if impression_data and 'competitors' in impression_data:
                            for comp in impression_data['competitors']:
                                comp_domain = comp.get('表示 URL ドメイン', '').replace("www.", "")
                                if comp_domain in domain or domain in comp_domain:
                                    lp_analysis['impression_share'] = comp.get('インプレッション シェア', 'N/A')
                                    lp_analysis['impression_share_value'] = comp.get('impression_share_value', 0)
                                    break
                        
                        # 検索キーワード情報を追加
                        lp_analysis['search_keyword'] = keyword
                        lp_analysis['source'] = "キーワード"
                        
                        # 結果に追加
                        similar_lps.append(lp_analysis)
                        logger.info(f"通常キーワードからのLP追加 ({len(similar_lps)}/7): {result_url}")
                        
                    except Exception as e:
                        logger.error(f"LP分析エラー {result_url}: {str(e)}")
                        continue
                
                # サーバー負荷軽減のため少し待機
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"検索エラー {keyword}: {str(e)}")
                continue
    
    logger.info(f"合計{len(similar_lps)}件の類似LPを取得しました")
    
    # 口コミ検索
    review_results = {}
    try:
        if original_url:
            logger.info(f"元サイト「{original_url}」の口コミ検索を実行")
            reviews = search_lp_reviews(original_url)
            if reviews:
                review_results['original'] = reviews
                logger.info(f"元サイトの口コミ{len(reviews)}件を取得")
    except Exception as e:
        logger.error(f"口コミ検索エラー: {str(e)}")
    
    # 結果をまとめて返す
    return {
        'similar_lps': similar_lps,
        'review_results': review_results,
        'impression_data': impression_data
    }

# 元の関数名を維持しながら、内部で新しい関数を呼び出す
def find_similar_landing_pages(original_url, original_analysis, impression_data=None, additional_keywords=None):
    """
    元のLPに類似したランディングページを検索する（AIによる選別機能を追加）
    """
    # 設定ファイルまたは環境変数でAI選別機能を無効化できるようにする
    use_ai_filtering = os.environ.get("USE_AI_FILTERING", "true").lower() == "true"
    
    if use_ai_filtering:
        try:
            logger.info("AI選別による競合LP検出を開始します")
            return find_similar_landing_pages_with_ai_filtering(original_url, original_analysis, impression_data, additional_keywords)
        except Exception as e:
            logger.error(f"AI選別による競合LP検出に失敗しました: {str(e)}、従来の方法を使用します")
            return find_similar_landing_pages_original(original_url, original_analysis, impression_data, additional_keywords)
    else:
        logger.info("従来の方法による競合LP検出を開始します")
        return find_similar_landing_pages_original(original_url, original_analysis, impression_data, additional_keywords)
         