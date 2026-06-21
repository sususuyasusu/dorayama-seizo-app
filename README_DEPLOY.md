# どら山 製造アプリ — ネット配信（Render）手順

店舗スタッフのスマホで使えるように、Render（無料枠可）へ載せる手順。

## 何を持たせるか
- このフォルダ一式（app.py / *_layer.py / made_store.py / templates / requirements.txt / Procfile）
- サービスアカウントの鍵JSON（ファイルは置かず、Renderの環境変数に貼る）

## 手順（ブラウザ作業・本人）
1. このフォルダをGitHubの非公開リポジトリに上げる（または Render の手動デプロイを使う）。
2. Render → New → Web Service → リポジトリを接続 → Runtime「Python」。
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python app.py`
   - Plan: Free
3. 環境変数を1つ追加：
   - キー: `DORAYAMA_SA_CRED_JSON`
   - 値: サービスアカウント鍵JSONの中身をまるごと貼り付け
     （鍵: `dorayama-sheets-bot@dw-dorayama-automation.iam.gserviceaccount.com`、
      元ファイル: dw_budget_profit_sheets_automation/config/google_credentials.json）
   - PORT は Render が自動で渡すので設定不要。
4. Deploy。発行されたURL（例 https://dorayama-seizo-app.onrender.com）をスマホのホーム画面に追加。

## 保存先（作った数）
- 「作った数」は製造表シート内の専用タブ `_app_made` に保存（既存の表・数式には不干渉）。
- ネット配信でもMacが落ちても値は消えない。

## 注意
- 無料枠はアクセスが無いと一時停止し、初回アクセスが遅い（数十秒）。`render-bot-keepalive` で定期アクセスすれば回避可。
- 鍵JSONはGitHubに絶対に上げない（.gitignoreで *.json を除外済み）。
