# Seed Radar 設計図

arXiv の宇宙・量子・AIの最新論文を取得し、それぞれの技術が「どんな巨大ビジネスやスタートアップの種になるか」を英語と日本語で抽出・構造化するシステム。

最終更新: 2026-11-17

## 全体の流れ

```
arXiv API ─▶ 1. 取得 ─▶ 2. 抽出 ─▶ 3. 検証・統合 ─▶ 4. 公開
             fetch       エージェント   validate / merge   index.html
             papers.json  analysis/*.json  seeds.json         GitHub Pages
```

| 段階 | 役割 | 実装 |
|---|---|---|
| 1. 取得 | arXiv API から分野ごとの最新論文を取り、同じ形に整える | `seedradar/arxiv.py` |
| 2. 抽出 | 題名と要旨から、事業の種を英語と日本語で構造化する | コーディングエージェント(Claude Code / Codex)が `seedradar/prompts/update.md` に沿って書く |
| 3. 検証・統合 | 抽出結果の形を検証し、公開用のデータにまとめる | `seedradar/seeds.py` |
| 4. 公開 | 絞り込み・並べ替え・言語の切り替えができる画面 | `index.html`(サーバーなし) |

### 費用をかけない構成

- 抽出に外部のLLM APIを使わない。契約済みの Claude Code や Codex に、手順書を渡して分析させる
- そのため APIキーは不要で、追加の課金もない
- 代わりに、更新は人が1回指示する必要がある(下の「毎日の更新」)

## ディレクトリ

```
index.html             Seed Radar の画面(data/seeds.json を読む)
data/
  papers.json          取得した最新の論文(要旨つき)
  analysis/DATE.json   その日に書いた分析
  seeds.json           公開用。これまでの分析をすべてまとめたもの
seedradar/
  arxiv.py             取得: URL組み立て、Atomフィードの解析、リトライ、分野の定義
  seeds.py             分析の検証、seeds.json への統合、未分析の論文の一覧
  analyze.py           毎日の自動実行での分析(Claude API)
  cli.py               コマンド: fetch / pending / validate / merge / remove / daily
  prompts/criteria.md  分析の基準(手での分析と自動実行で共通)
  prompts/update.md    エージェントが手で更新するときの手順書
.claude/commands/seed-radar.md   Claude Code の /seed-radar コマンド
.github/workflows/daily-radar.yml  毎日の自動更新
requirements.txt     自動更新だけが使う依存(anthropic)
tests/                 テスト(ネットワークに依存しない)
log/                   開発の記録ページ
mock/                  看護記録の要約の見本
```

## 毎日の自動更新(GitHub Actions)

`.github/workflows/daily-radar.yml` が、平日の 02:17 UTC(日本時間 11:17)に次を実行する。手動でも実行できる(Actions タブ → Daily Seed Radar → Run workflow)。

```
1. python -m unittest                      更新前のテスト
2. python -m seedradar daily               取得 → 未分析の抽出 → Claude API で分析 → 検証 → seeds.json に統合
3. python -m unittest                      更新後のテスト(公開データの検証を含む)
4. data/ だけをコミットして反映            PR を作る(既定)か、main に直接 push する
```

- **時刻**: arXiv は米国東部時間の日〜木 20:00 に新着を告知する(冬は 01:00 UTC、夏時間は 00:00 UTC)。日本時間 9:00(00:00 UTC)だと冬は告知前になるので、余裕をみて 02:17 UTC にしている。週末は新着がないので動かさない
- **取得**: arXiv API が 429 などで答えないときは RSS に切り替わる(上の「1. 取得」)。GitHub のランナーも共有IPなので、429 は起こりうる
- **分析**: `seedradar/analyze.py` が論文1本につき1回 Claude API を呼ぶ
  - モデルは `claude-opus-5-5`、effort は `medium`(リポジトリ変数で変えられる)
  - 指示は `seedradar/prompts/criteria.md`(エージェントが手で分析するときと同じ基準)
  - 出力は JSON スキーマで形を固定し、受け取ったあと `validate_entry` で検証する。だめなら1回だけやり直す
  - 安全上の理由でモデルが断ったときは、API 側で推奨の代わりのモデルに切り替える(`fallbacks: "default"`)。それでも断られた論文は飛ばし、次の回にもう一度試す
  - 種がない論文は、スコアをすべて1にそろえる(基準どおり)
- **何も変わらない日**: 新着がない、またはすべて分析済みなら、コミットも PR も作らない
- **失敗したとき**: 取得の失敗、または1本も分析できなかったときは、ワークフローが失敗になる(GitHub から通知が届く)。公開データは変わらない
- **反映**: `data/` 以外のファイルはコミットしない
  - `pr`(既定): `radar/daily-日付-実行ID` ブランチを作り、PR を出す。PR の本文に、分析した論文とスコアの表が入る。オーナーがマージすると公開される
  - `direct`: main に直接 push する。完全に自動になるが、分析を人が見ないまま公開される

### 費用の目安

- Claude API の利用料がかかる(Pro プランなどの契約とは別の、従量課金)
- 1日最大20本(4分野 × 5本)。1本あたり入力は約2千トークン、出力は思考を含めて数千トークンなので、Claude Opus 5.5(入力 $4 / 出力 $20、100万トークンあたり)で **1日 1.3ドル前後、月 25〜30ドル前後** が目安(実際の値は Console の使用量で確認する)
- 費用を下げるなら、リポジトリ変数 `SEEDRADAR_MODEL` に `claude-sonnet-5-5` を入れる(分析の質は確認が必要)
- Console で、月の利用上限を必ず設定する

### オーナーが設定すること

| どこで | 何を | 必須 |
|---|---|---|
| Anthropic Console | API キーを作る。月の利用上限を設定する | 必須 |
| GitHub → Settings → Secrets and variables → Actions → Secrets | `ANTHROPIC_API_KEY` に API キーを入れる | 必須 |
| GitHub → Settings → Actions → General → Workflow permissions | 「Read and write permissions」と「Allow GitHub Actions to create and approve pull requests」をオンにする | `pr` で必須 |
| GitHub → Settings → Secrets and variables → Actions → Variables | `RADAR_PUBLISH_MODE` = `pr` または `direct` | 任意(既定は `pr`) |
| 同上 | `SEEDRADAR_MODEL`(既定 `claude-opus-5-5`)、`SEEDRADAR_EFFORT`(既定 `medium`) | 任意 |

### 手動で同じことをする

```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # リポジトリには書かない
python -m seedradar daily
python -m unittest
```

API キーを使わずに、Claude Code や Codex に手で分析させる方法も残している(`/seed-radar`、手順は `seedradar/prompts/update.md`)。

## 1. 取得

```
python -m seedradar fetch                  # 各分野5本(既定)
python -m seedradar fetch --fields quantum ai --max 50
```

### 分野とarXivの検索条件

| キー | 分野 | カテゴリ | キーワード |
|---|---|---|---|
| `space` | 宇宙開発 / Space Technology | `astro-ph.IM` `physics.space-ph` `eess.SP` `eess.SY` `eess.IV` `cs.RO` `cs.NI` `cs.CV` | 要旨に次のどれかを含む: spacecraft, cubesat, satellite constellation, earth observation, remote sensing, on-orbit, in-orbit, space debris, low earth orbit, inter-satellite, satellite communication, satellite imagery |
| `settlement` | 宇宙移住 / Space Settlement | `astro-ph.EP` `astro-ph.IM` `physics.space-ph` `physics.med-ph` `physics.app-ph` `eess.SY` `cs.RO` `q-bio.TO` | 要旨に次のどれかを含む: space habitat, life support, in-situ resource utilization, lunar base, lunar surface, lunar regolith, mars habitat, space settlement, space radiation, astronaut, spaceflight, microgravity, space medicine, space exploration |
| `quantum` | 量子計算 / Quantum Computing | `quant-ph` | なし |
| `ai` | AI / Artificial Intelligence | `cs.AI` `cs.LG` `cs.CL` | なし。ただし題名か要旨に宇宙・量子の語(satellite, quantum など)を含む論文を先に並べる |

`settlement`(宇宙移住)は、オーナーがいちばん惹かれている分野。宇宙の毎日の新着の中にはほとんど出てこないので、独立した分野にして、惑星科学や医学物理まで広く探す。

`space` は、カテゴリだけで取ると基礎的な天文学に偏る。そのため、工学寄りのカテゴリ(信号処理、制御、画像、ロボット、通信、画像認識、観測機器)の中から、要旨に衛星・宇宙機のキーワードを含む論文を取る。
検索式は `(cat:A OR cat:B ...) AND (abs:x OR abs:"y z" ...)`。条件は `seedradar/arxiv.py` の `FIELDS` で変える。
arXiv の検索は、ハイフン入りの語などをゆるく解釈して、キーワードを含まない論文も返すことがある。そのため、キーワードのある分野は必要な数の4倍を取り、要旨にキーワードが本当に入っている論文だけを残す(RSS と同じ確認)。

### 出力(`papers.json`)

```json
{
  "fetched_at": "2026-11-17T05:40:00Z",
  "source": "arXiv API",
  "fields": { "quantum": { "label_en": "...", "label_ja": "...", "categories": ["quant-ph"] } },
  "count": 15,
  "papers": [
    {
      "arxiv_id": "2611.20756",
      "version": 1,
      "title": "...",
      "summary": "...",
      "authors": ["..."],
      "primary_category": "quant-ph",
      "categories": ["quant-ph", "cond-mat.str-el"],
      "published": "2026-11-16T09:05:12Z",
      "updated": "2026-11-16T09:05:12Z",
      "abs_url": "https://arxiv.org/abs/2611.20756",
      "pdf_url": "https://arxiv.org/pdf/2611.20756v1",
      "fields": ["quantum"]
    }
  ]
}
```

- 複数の分野に当てはまる論文は1件にまとめ、`fields` に両方を入れる
- 新しい順(`published` の降順)に並べる

### 設計上の決めごと

- 依存ライブラリなし(Python標準ライブラリのみ。Python 3.10以上)
- arXiv のルールに合わせ、リクエストの間隔は3秒
- **別ルート(RSS)**: API が答えないとき(429、5xx、通信エラー)は、arXiv 公式の RSS(`rss.arxiv.org`)に切り替える
  - 同じカテゴリの RSS を取り、要旨にキーワードを含む論文だけを残す(API の検索式と同じ条件)
  - RSS に載るのは、その日に告知された論文だけ。5本に満たないことがあり、週末や告知のない日は0本になる
  - RSS の日付は告知日で、投稿日ではない。差し替え(replace)の告知は取らない
  - 1つの分野で API が失敗したら、その回の残りの分野は API を呼ばずに RSS を使う
  - API も RSS もだめなときはエラーで止まり、`papers.json` は書き換えない
  - `--source api` / `--source rss` で、どちらかに固定できる。取得元は `papers.json` の各論文の `source` に残る
- 通信エラー、429、5xx は最大3回までリトライ。4xx はすぐ失敗させる
- arXiv がエラーを返したとき(エラー用のエントリ)は、例外にして止める

## 2. 抽出

- 基準と出力の形は `seedradar/prompts/update.md` に書いてある。実例は `data/analysis/2026-11-17.json`
- 読むのは題名と要旨だけ。要旨にない数字は作らない
- 無理に種を作らない。事業にならない論文は `business_seeds` を空にし、スコアをすべて1にする
- 市場規模の具体的な金額は書かない(出典を確認できないため)

## 3. 検証・統合

- `validate`: 必須の項目、英語と日本語の両方があること、スコアが1〜5の整数であること、選択肢の値を確認する
- `merge`: 論文の情報(題名、著者、リンクなど)と分析を1件にまとめ、`seeds.json` に追加する
  - 同じ論文をもう一度分析したら、新しい分析で置き換える
  - `papers.json` にない論文や、版(version)が違う分析は拒否する
  - 公開用のデータには要旨を入れない(画面から arXiv にリンクする)
- `remove`: 公開用のデータから論文を外す(`python -m seedradar remove ID...`)。分野の条件を変えて、対象外になった論文を外すときに使う
- `seeds.json` は過去の分をすべて持つ。新しい順に並ぶ

## 4. 公開(`index.html`)

- 英語 / 日本語の切り替え(選んだ言語はブラウザに保存。初回はブラウザの言語に合わせる)
- 分野の絞り込み、「事業の種がある論文だけ」の絞り込み
- 並べ替え: 合計スコア、市場規模、実現性、起業向き、新しい順
- 各論文に、技術の要約、事業の種(顧客・市場・なぜ今か・参入障壁・市場に出るまで)、注意点、確信度、arXiv へのリンク
- **Startup Brief のコピー**: 事業の種がある論文の「詳細」から、企画メモ(Markdown)を表示中の言語でクリップボードにコピーする。コピーできない環境では、文章を選択できる欄を出す
- **Daily Alert のウェイトリスト**: ページの一番上の色付きの帯に「Get Daily High-Score Seeds」ボタン、ヘッダーにも「Daily Alert」ボタンを置いている。今は GitHub の Issue 作成画面(題名と本文を入力済み)を開く。`index.html` の `WAITLIST_FORM_URL` に Google フォームなどのURLを入れると、そちらに切り替わる。登録の数が、有料版の需要の目安になる
- 明るい画面と暗い画面の両方に対応。スマホの幅でも崩れない
- フッターに、AIによる推定であることの注意書きと、arXiv への謝辞を載せている

## テスト

```
python -m unittest
```

- ネットワークには出ない。`tests/fixtures/feed.xml`(作り物のフィード)を使う
- 取得: URLの組み立て、フィードの解析、分野をまたぐ重複のまとめ、リトライ
- 分析: 検証のルール、統合、版の不一致の拒否、未分析の一覧
- 公開データ: コミットされた `data/analysis/*.json` と `data/seeds.json` が、いつも検証を通ること

## 進め方

| 段階 | 内容 | 状態 |
|---|---|---|
| M1 | 取得とテスト | 完了 |
| M2 | 抽出の手順と、最初の15本の分析 | 完了 |
| M3 | 検証・統合 | 完了 |
| M4 | 公開ページ | 完了 |
| M5 | 毎日の更新を自動にする(GitHub Actions + Claude API) | 完了(初回の実行は未確認) |
| M6 | アクセス解析、週1回のまとめ | |

## 今の限界

- 分析は題名と要旨だけにもとづく推定。本文は読んでいない
- 市場や顧客の記述は、出典で確認したものではない
- 「上位5本」は、各分野の新しい順の5本。重要度で選んでいるわけではない
- 画面は `seeds.json` を全部読み込む。数千本を超えたら、月ごとに分けるなどの対応が要る
