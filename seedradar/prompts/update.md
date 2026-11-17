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

- 読むのは題名と要旨だけ。要旨に書かれていない実験結果や数字を作らない
- **無理に種を作らない。** 基礎研究の多くは、近い将来の事業にならない。その場合は `business_seeds` を空の配列にし、スコアはすべて 1 にする
- 市場規模の具体的な金額は書かない(出典を確認できないため)。市場の性質と、誰がお金を払うかを書く
- 参入障壁が弱いときは、弱いと書く。大企業や既存の事業者が同じことをできるなら、それも書く
- `caveats` には、この分析が外れるとしたらどこかを書く(シミュレーションだけ、ベンチマークが少ない、実機が1台だけ、など)
- 英語と日本語は、同じ内容を書く。日本語は直訳ではなく、自然な文にする
- 専門用語は、最初に出たところで短く説明する

### スコア(1〜5)

| キー | 1 | 3 | 5 |
|---|---|---|---|
| `market_size` | 顧客がいない、またはごく少数 | まとまった専門市場 | 多くの産業にまたがる巨大市場 |
| `feasibility` | 理論だけ、または実用まで10年以上 | 試作や実証はある。製品化に数年 | 今ある技術ですぐ作れる |
| `startup_fit` | 大企業や研究機関しか扱えない | 小さな会社でも始められるが、障壁は弱い | 小さな会社が先に取れて、守れる |

### 確信度

- `high`: 要旨からはっきり判断できる
- `medium`: 妥当だと思うが、本文や市場の調査で変わりうる
- `low`: 推測が多い

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
