# iScreeM

研究用途のローカル Web アプリです。React/3Dmol.js の Viewer と FastAPI、SQLite、Redis/RQ ワーカーを組み合わせ、PDB 解析、P2Rank、リガンド調製、smina/gnina docking の実 CLI 実行を一つの画面から管理します。

> **Research software:** iScreeMの計算結果は、実験的検証や専門家の確認を
> 代替するものではありません。

## Quick start

```bash
cp .env.example .env
python scripts/setup_dependencies.py
conda run -n iScreeM uvicorn app.main:app --reload --app-dir backend --port 8000
```

ブラウザで `http://localhost:5175` を開きます。外部 CLI はホストまたはコンテナ内の `PATH` に必要です。起動後の **System tools** 表示は実際の `--version` / `--help` probe 結果で、未検出ツールに依存する操作は無効になります。

ローカル環境で外部 CLI の場所を指定する場合は `.env` の `MDS_P2RANK_COMMAND` と `MDS_SMINA_COMMAND` を設定します。公開用の `.env.example` はリポジトリ内の相対パスを例示し、個人環境の絶対パスは含みません。

`scripts/setup_dependencies.py` はP2Rank・sminaの実行確認を行い、Redisが未導入ならmacOSではHomebrewでインストールし、Redisサービスを起動して `PONG` 応答まで確認します。P2Rankとsminaは公式配布物の場所を `.env` または `--p2rank` / `--smina` で指定してください。これらの実行ファイルとP2RankモデルはGitリポジトリには含まれません。

`scripts/setup_dependencies.py` は、PDBFixer・OpenMM・PDB2PQR・RDKit・Meekoを含む `iScreeM` conda環境を作成または更新します。protein preparationは、PDBFixerでSEQRES由来の欠損残基・原子を補完し、PDB2PQRでpH 7.4のプロトン化を行います。欠損を削除して処理を継続するフォールバックはありません。

`smina`と`gnina`はPDB受容体とSDFリガンドを直接受け取れるため、protein preparationの出力はPDBです。PDBQTはAutoDock Vina互換用の任意成果物としてのみ保持します。

ローカル開発:

```bash
conda run -n iScreeM pip install -e './backend[dev,chem]'
conda run -n iScreeM uvicorn app.main:app --reload --app-dir backend --port 8000

cd frontend
npm install
npm run dev
```

別ターミナルで Redis と worker を起動します。

```bash
redis-server
# RDKit/Meekoを使うリガンド調製は、forkしないSimpleWorkerで実行します。
conda run -n iScreeM rq worker docking default --worker-class rq.worker.SimpleWorker --url redis://localhost:6379/0 --path backend
```

## 現在の実装範囲

- 永続 Project、PDB / CIF / mmCIF upload、構造統計、元ファイル保持
- Project内の複数receptor選択、receptorごとのprotein preparation / pocket / grid管理、関連ファイル削除
- 構造ファイルのクリック選択またはドラッグ＆ドロップアップロード
- 3Dmol.js Viewer（cartoon/stick/sphere、chain 色分け、水・hetero・metal 表示、PNG、fit/reset）
- P2Rank の RQ background job、結果 CSV/points 解析、Pocket 表示
- Pocket 点群または残基座標からの Grid 作成（片側 4 Å、最小 18 Å）、編集・保存・複製
- SMILES batch の RDKit/Meeko 調製ジョブと descriptor 保存
- smina / gnina Adapter、許可リスト式引数、ジョブ snapshot、Ligand 単位の結果保存
- Job monitor、永続状態、SSE + polling fallback、キャンセル要求
- 実行ファイル probe API（存在だけで available にしない）

CLI と chemical dependencies がない環境では、その機能は実行不能として明示されます。`backend/tests` の unit test は外部 CLI を mock 化し、`pytest -m integration` は実際のツールがある場合だけ実行します。

## License

iScreeMの独自コードと独自アセットは[MIT License](LICENSE)で公開します。
第三者ソフトウェアはそれぞれのライセンスに従い、MITへ再ライセンスされません。
配布範囲、直接依存、smina/gninaのGPL対応、Redisのバージョン別条件は
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)に記載しています。

このリポジトリは外部CLIの実行ファイル、P2Rankモデル、分子構造、計算結果、
ユーザープロジェクトを配布しません。将来それらを含むインストーラーやコンテナを
配布する場合は、成果物単位で第三者ライセンスを再監査してください。
