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
  cli.py               コマンド: fetch / pending / validate / merge / remove
  prompts/update.md    更新の手順書(分析の基準と出力の形)
.claude/commands/seed-radar.md   Claude Code の /seed-radar コマンド
tests/                 テスト(ネットワークに依存しない)
log/                   開発の記録ページ
mock/                  看護記録の要約の見本
```

## 毎日の更新

Claude Code では `/seed-radar`、Codex では「Seed Radar を更新して」と指示する。エージェントは次を実行する。

```
python -m seedradar fetch                              # 最新の論文を取得(各分野5本)
python -m seedradar pending                            # まだ分析していない論文を表示
(エージェントが data/analysis/DATE.json を書く)
python -m seedradar validate data/analysis/DATE.json   # 形を検証
python -m seedradar merge data/analysis/DATE.json      # seeds.json に統合
python -m unittest
```

PRをマージすると、GitHub Pages に反映される。

## 1. 取得

```
python -m seedradar fetch                  # 各分野5本(既定)
python -m seedradar fetch --fields quantum ai --max 50
```

### 分野とarXivの検索条件

| キー | 分野 | カテゴリ | キーワード |
|---|---|---|---|
| `space` | 宇宙開発 / Space Technology | `astro-ph.IM` `physics.space-ph` `eess.SP` `eess.SY` `eess.IV` `cs.RO` `cs.NI` `cs.CV` | 要旨に次のどれかを含む: spacecraft, cubesat, satellite constellation, earth observation, remote sensing, on-orbit, in-orbit, space debris, low earth orbit, inter-satellite, satellite communication, satellite imagery |
| `quantum` | 量子計算 / Quantum Computing | `quant-ph` | なし |
| `ai` | AI / Artificial Intelligence | `cs.AI` `cs.LG` `cs.CL` | なし |

`space` は、カテゴリだけで取ると基礎的な天文学に偏る。そのため、工学寄りのカテゴリ(信号処理、制御、画像、ロボット、通信、画像認識、観測機器)の中から、要旨に衛星・宇宙機のキーワードを含む論文を取る。
検索式は `(cat:A OR cat:B ...) AND (abs:x OR abs:"y z" ...)`。条件は `seedradar/arxiv.py` の `FIELDS` で変える。

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
- **Daily Alert のウェイトリスト**: 「Get Daily High-Score Seeds」ボタン。今は GitHub の Issue 作成画面(題名と本文を入力済み)を開く。`index.html` の `WAITLIST_FORM_URL` に Google フォームなどのURLを入れると、そちらに切り替わる。登録の数が、有料版の需要の目安になる
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
| M5 | 毎日の更新を自動にする | 次 |
| M6 | アクセス解析、週1回のまとめ | |

### M5 について

GitHub Actions だけでは自動にできない(抽出にエージェントが必要なため)。候補は2つ。

1. Claude Code のクラウドのスケジュール実行で、毎日 `/seed-radar` を動かす(追加の課金なし。PRのマージは人がやる)
2. LLM API を使って GitHub Actions で全自動にする(APIの費用がかかる)

## 今の限界

- 分析は題名と要旨だけにもとづく推定。本文は読んでいない
- 市場や顧客の記述は、出典で確認したものではない
- 「上位5本」は、各分野の新しい順の5本。重要度で選んでいるわけではない
- 画面は `seeds.json` を全部読み込む。数千本を超えたら、月ごとに分けるなどの対応が要る
