# HFE キャラクター作成ツール

[中文](README.md) | [English](README.en.md) | **日本語**

HFE / Hero Fighter のカスタムキャラクターを作成・出力するための
Windows GUI ツールです。FFDec、HFWorkshop、SPT/LMI データ、SWF パッチの
手順に詳しくないプレイヤーでも、画面上でテンプレートを選び、テクスチャや
item フレームを設定し、検証して出力できるようにすることを目標としています。

このリポジトリにはツールのソースコード、テスト、公開ドキュメントのみが
含まれます。元のゲームファイル、サードパーティ製バイナリ、プレイヤーの
ローカルプロジェクト、出力済みゲームファイルは含まれていません。

## 主な機能

- カスタムキャラクタープロジェクトの作成と管理。
- 先に対象 HFE EXE/SWF を選択し、そのバージョンから利用可能な
  キャラクターテンプレートと item データを読み込み。
- キャラクター ID、名称、中国語名、説明、HP、MP、防御などの基本情報を編集。
- キャラクターのアクションとフレームごとに item 生成を設定。
- キャラクター各部位のテクスチャ参照元を選択（HF-EX 0.2.5 の Lucas の骨格に合わせて
  切り直された版から取るため、HFE などに出力してもずれない）。
- 出力前に検証し、明らかに非互換な設定を停止。
- すでに改造済みの対象バージョンへキャラクターを追加。
- キャラクター属性、テクスチャ組み合わせ、item フレーム設定をランダム生成。
- 指定フレーム数遅れてキャラクターの動きを再現する残像を追加。残像がダメージを与えるか、item を再発射するかは個別に設定可能。
- アクション一覧に中国語の技名を表示（テンプレートの実際のアクションからキャラクターを判定）し、中国語でも検索可能。
- 編集中のリアルタイム検証：対象ゲームで使用済みのキャラクター ID、対象バージョンに存在しない item やアクション、ゲームで表示できない簡体字（ゲームは繁体字 Big5 で表示）、参照元キャラクターに該当部位のパーツがないテクスチャ差し替え。
- 出力したゲームを土台にワンクリックでキャラクターを追加。プロジェクトの複製と削除（`projects/_已删除` へ移動）。

## 画面プレビュー

### 基本情報

![基本情報ページ](docs/images/edit-character.png)

### テクスチャ

![テクスチャページ](docs/images/textures.png)

### スキルと item

![スキル item ページ](docs/images/skills.png)

### 残像

![残像ページ](docs/images/afterimage.png)

### 検証と出力

![検証と出力ページ](docs/images/validation-export.png)

## リポジトリ構成

```text
src/hfe_character_tool/   ツールのソースコード
tests/                    自動テスト
docs/                     ユーザードキュメント
scripts/                  ローカル用パッケージングスクリプト
vendor/README.md          外部依存関係の配置説明
pyproject.toml            Python プロジェクト設定
```

## 含まれないもの

次のファイルは意図的にリポジトリから除外されており、各自でローカルに
準備する必要があります。

- HFE / Hero Fighter のオリジナルまたは改造済みゲームファイル。
- FFDec、HFWorkshop、SA.exe、playerglobal.swc などのサードパーティ製ツール。
- Java/JDK 実行環境。
- `projects/` 配下のローカルキャラクタープロジェクト。
- `output/` 配下の出力 EXE/SWF、キャッシュ、ログ。

## 外部依存関係の配置

ゲームを出力する前に、リポジトリ直下へ次の構成で依存関係を準備します。

```text
vendor/
  FFDec/
    ffdec.jar
  HFWorkshop/
    HFWorkshop.exe
  projector/
    SA.exe
  playerGlobal/
    playerglobal.swc
  original_game/
    HFE v1.0.2.exe
    HF-EX V 0.2.5.exe
```

Java/JDK はシステム PATH にインストールするか、リポジトリ付近の次の
ディレクトリのいずれかへ置くことができます。

```text
runtime/jdk/
runtime/jdk-*/
jdk/
jdk-*/
```

詳細は `docs/dependencies.md` を参照してください。

## 開発環境

Python 3.8 以降を推奨します。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

テストと検査を実行します。

```powershell
python -m pytest -q
python -m mypy src tests
python -m ruff check src tests
```

ソースから直接起動する場合：

```powershell
$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"
python -m hfe_character_tool
```

## Windows EXE のローカル作成

最初にパッケージング依存関係をインストールします。

```powershell
python -m pip install -e ".[package]"
```

パッケージングスクリプトを実行します。

```powershell
.\scripts\build_windows.ps1
```

ビルド結果は `output/tool_dist/` に書き出されます。`output/` は既定で
Git の管理対象外です。

## 公開前の注意

- `vendor/`、`runtime/`、`projects/`、`output/` をコミットしないでください。
- 出力済みのゲーム EXE/SWF をコミットしないでください。
- サードパーティ製ツール、元のゲームファイル、個人用ローカル設定を
  コミットしないでください。
- 公開前に少なくとも `pytest`、`mypy`、`ruff` を実行してください。

## ライセンス

公開リリース前に、配布方針に合ったライセンスファイルを追加してください。
