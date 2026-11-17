# Seed Radar 設計図

arXiv の宇宙・量子・AIの最新論文を取得し、それぞれの技術が「どんな巨大ビジネスやスタートアップの種になるか」を英語と日本語で抽出・構造化するシステム。

最終更新: 2026-11-17

## 全体の流れ

```
arXiv API ──▶ 1. 取得 ──▶ 2. 抽出(LLM) ──▶ 3. 保存 ──▶ 4. 公開
              fetch        analyze           store        publish
              papers.json  seeds.json        履歴つき     Webページ
```

| 段階 | 役割 | 状態 |
|---|---|---|
| 1. 取得 | arXiv API から分野ごとの最新論文を取り、同じ形に整える | **実装済み**(`seedradar/arxiv.py`) |
| 2. 抽出 | 論文の要旨から、ビジネスの種を英語と日本語で構造化する | 未実装 |
| 3. 保存 | 処理済みの論文を記録し、同じ論文を二度処理しない | 未実装 |
| 4. 公開 | 一覧・絞り込みができるページを GitHub Pages で公開する | 未実装 |

## ディレクトリ

```
seedradar/
  arxiv.py      取得: URL組み立て、Atomフィードの解析、リトライ、分野の定義
  cli.py        コマンド: python -m seedradar fetch
  analyze.py    (予定)抽出: LLM呼び出し、出力の検証
  store.py      (予定)保存: 処理済みIDの管理、日付ごとのファイル
tests/
  fixtures/     テスト用の固定データ(ネットワークに依存しない)
data/           実行結果の出力先(.gitignore で除外)
```

## 1. 取得(実装済み)

```
python -m seedradar fetch --max 10 --out data/papers.json
python -m seedradar fetch --fields quantum ai --max 50
```

### 分野とarXivカテゴリ

| キー | 分野 | カテゴリ |
|---|---|---|
| `space` | 宇宙開発 / Space & Astrophysics | `astro-ph.EP` `astro-ph.IM` `physics.space-ph` |
| `quantum` | 量子計算 / Quantum Computing | `quant-ph` |
| `ai` | AI / Artificial Intelligence | `cs.AI` `cs.LG` `cs.CL` |

`space` は、事業に近い3カテゴリ(惑星科学、観測機器・手法、宇宙物理)に絞っている。宇宙論や銀河などを足すときは `seedradar/arxiv.py` の `FIELDS` を変える。

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
- 通信エラー、429、5xx は最大3回までリトライ。4xx はすぐ失敗させる
- arXiv がエラーを返したとき(エラー用のエントリ)は、例外にして止める

## 2. 抽出(次に作る)

論文1本の題名と要旨を LLM に渡し、次の形のJSONを受け取る。

```json
{
  "arxiv_id": "2611.20756",
  "version": 1,
  "technology": { "en": "...", "ja": "..." },
  "breakthrough": { "en": "何が新しくできるようになったか", "ja": "..." },
  "business_seeds": [
    {
      "title": { "en": "...", "ja": "..." },
      "customer": { "en": "誰が買うか", "ja": "..." },
      "market": { "en": "どの市場か、どのくらい大きいか", "ja": "..." },
      "why_now": { "en": "...", "ja": "..." },
      "time_to_market": "now | 1-3y | 3-10y | 10y+",
      "moat": { "en": "参入障壁", "ja": "..." }
    }
  ],
  "scores": { "market_size": 1, "feasibility": 1, "startup_fit": 1 },
  "confidence": "low | medium | high",
  "caveats": { "en": "この推定の弱いところ", "ja": "..." }
}
```

- スコアは1〜5。一覧の並べ替えに使う
- 多くの基礎研究は、すぐには事業にならない。無理に種を作らせず、`business_seeds` が空でもよいことにする
- 市場規模などの数字は LLM の推定であり、出典つきの事実ではない。ページにもそう明記する
- 出力はJSONスキーマで検証し、合わないものは1回だけ再試行して、それでもだめなら捨てて記録する

### コストの管理

- 渡すのは題名と要旨だけ(PDF本文は読まない)
- `arxiv_id` と `version` をキーにして、処理済みの論文は飛ばす
- 1回の実行で処理する本数に上限をつける(`--limit`)
- APIキーは環境変数で渡す。リポジトリには入れない

## 3. 保存

- `data/seeds/YYYY-MM-DD.json` に日ごとの結果、`data/seeds/index.json` に処理済みIDの一覧
- 公開するときは、公開用のブランチかフォルダにJSONを置く(次の「公開」で決める)

## 4. 公開

- 静的なページ(HTMLとJavaScript)が `seeds.json` を読んで表示する。サーバーは持たない
- 機能: 分野の絞り込み、スコア順の並べ替え、英語と日本語の切り替え、元の論文へのリンク
- 毎日の更新は GitHub Actions の定期実行で行う(取得 → 抽出 → JSONを更新 → Pagesに反映)
- arXiv の利用条件に合わせ、ページに「Thank you to arXiv for use of its open access interoperability.」と、各論文へのリンクを載せる

## テスト

```
python -m unittest
```

- ネットワークには出ない。`tests/fixtures/feed.xml`(作り物のフィード)を使う
- URLの組み立て、フィードの解析、分野をまたぐ重複のまとめ、リトライ、CLIの出力を確認している

## 進め方

| 段階 | 内容 | 状態 |
|---|---|---|
| M1 | 取得とテスト | 完了 |
| M2 | 抽出(LLM)。まず10本で出力の質を確かめる | 次 |
| M3 | 保存と、処理済みの管理 | |
| M4 | 公開ページ | |
| M5 | GitHub Actions で毎日自動更新 | |

## オーナーが決めること

1. **抽出に使うLLM**: Claude API か OpenAI API か。APIキーの用意と、1ヶ月の上限金額
2. **1日に処理する本数**: 3分野で1日に数百本出る。全部か、分野ごとに上位だけか
3. **公開の形**: 自分用のダッシュボードか、誰でも見られるページか
