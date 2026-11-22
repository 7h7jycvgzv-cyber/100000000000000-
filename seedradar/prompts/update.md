# Seed Radar の更新手順

この手順は、Claude Code や Codex などのコーディングエージェントが実行する。
外部のLLM APIは呼ばない。論文を読んで分析を書くのは、エージェント自身。

## 手順

1. `main` の最新から、ブランチ `radar-update-YYYY-MM-DD` を作る
2. 最新の論文を取得する(各分野5本)
   ```
   python -m seedradar fetch
   ```
3. まだ分析していない論文を確認する
   ```
   python -m seedradar pending
   ```
   `count` が 0 なら、新しい論文はない。そう報告して終わる
4. `pending` に出た論文それぞれについて、題名と要旨を読み、下の「分析の基準」と「出力の形」に沿って分析を書く。
   書き出し先は `data/analysis/YYYY-MM-DD.json`(同じ日のファイルがあれば、`-2` などをつける)
5. 検証して、公開用のデータに統合する
   ```
   python -m seedradar validate data/analysis/YYYY-MM-DD.json
   python -m seedradar merge data/analysis/YYYY-MM-DD.json
   ```
6. テストを通す
   ```
   python -m unittest
   ```
7. `data/` の変更をコミットしてpushし、PRを出す。何本を分析し、そのうち何本に事業の種があったかを報告する

## 分析の基準

`seedradar/prompts/criteria.md` を読み、その基準どおりに書く(毎日の自動実行も同じ基準を使う)。

## 出力の形

```json
{
  "analyzed_at": "YYYY-MM-DD",
  "analyzer": "Claude Code (title and abstract only)",
  "papers": [
    {
      "arxiv_id": "2611.20759",
      "version": 1,
      "technology": { "en": "...", "ja": "..." },
      "breakthrough": { "en": "...", "ja": "..." },
      "business_seeds": [
        {
          "title": { "en": "...", "ja": "..." },
          "customer": { "en": "...", "ja": "..." },
          "market": { "en": "...", "ja": "..." },
          "why_now": { "en": "...", "ja": "..." },
          "time_to_market": "now | 1-3y | 3-10y | 10y+",
          "moat": { "en": "...", "ja": "..." }
        }
      ],
      "scores": { "market_size": 1, "feasibility": 1, "startup_fit": 1 },
      "confidence": "low | medium | high",
      "caveats": { "en": "...", "ja": "..." }
    }
  ]
}
```

- `arxiv_id` と `version` は、`pending` の出力と同じ値にする
- `business_seeds` は、1本の論文につき最大3件
- `analyzer` には、分析したエージェントの名前を書く(モデルの型番は書かない)
- 書いた分析の実例は `data/analysis/2026-11-17.json` にある
