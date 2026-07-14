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
- キャラクター各部位のテクスチャ参照元を選択。
- 出力前に検証し、明らかに非互換な設定を停止。
- すでに改造済みの対象バージョンへキャラクターを追加。
- キャラクター属性、テクスチャ組み合わせ、item フレーム設定をランダム生成。

## 画面プレビュー

### キャラクター編集

![キャラクター編集ページ](docs/images/edit-character.png)

### テクスチャとスキル

![テクスチャとスキルページ](docs/images/items-and-textures.png)

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
