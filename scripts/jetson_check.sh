#!/usr/bin/env bash
# 確認 Jetson 的規格與現況，回答會議清單 B1（型號、記憶體、JetPack、磁碟）、B5（外網），
# 並列出 B2（上面還跑什麼）的線索。見 docs/20_Jetson部署流程.md 第 0 步。
#
# 只讀：不安裝、不改設定、不用 sudo（需要 root 的項目改印「沒有權限」）。
# 不印出 IP、MAC、主機名稱與使用者名稱，輸出可以直接貼到 issue 或對話裡。
#
# 用法：bash scripts/jetson_check.sh            # 含外網測試（連 pypi.org、github.com）
#       bash scripts/jetson_check.sh --offline  # 不連外
# 結束碼：盤點跑完一律是 0，判斷寫在最後的「摘要」；參數錯誤是 2。
#
# 測試用：JETSON_CHECK_ROOT 設成一個資料夾時，系統檔（/etc、/proc、/usr/local/cuda 底下）改從那裡讀。

set -u
export LC_ALL=C

OFFLINE=0
case "$#:${1:-}" in
  1:--offline) OFFLINE=1 ;;
  0:) ;;
  *) echo "用法：bash scripts/jetson_check.sh [--offline]" >&2; exit 2 ;;
esac

ROOT=${JETSON_CHECK_ROOT:-}
MIN_PY=3.11   # pyproject.toml 的 requires-python
MIN_PY_TUPLE="(${MIN_PY/./, })"

NOTES=()
note() { NOTES+=("$1"); }
has() { command -v "$1" >/dev/null 2>&1; }
section() { printf '\n## %s\n' "$1"; }
row() { printf -- '- %s：%s\n' "$1" "$2"; }

echo "# Jetson 盤點（scripts/jetson_check.sh，$(date -u +%Y-%m-%dT%H:%MZ)）"

# ── B1：型號與 JetPack ─────────────────────────────
section "型號與系統（B1）"
model=""
[ -r "$ROOT/proc/device-tree/model" ] && model=$(tr -d '\0' <"$ROOT/proc/device-tree/model")
row "型號" "${model:-讀不到（/proc/device-tree/model）}"
if [ -r "$ROOT/etc/nv_tegra_release" ]; then
  row "L4T" "$(head -1 "$ROOT/etc/nv_tegra_release")"
else
  row "L4T" "沒有 /etc/nv_tegra_release（可能不是 Jetson）"
  note "不是 Jetson，或 JetPack 沒裝好：下面的結果只能參考。"
fi
jetpack=$(dpkg-query -W -f='${Version}' nvidia-jetpack 2>/dev/null || true)
row "JetPack（nvidia-jetpack 套件）" "${jetpack:-沒有安裝這個套件（不一定代表沒有 JetPack，看上面的 L4T）}"
# shellcheck disable=SC1091  # 執行時才讀系統檔
os=$( . "$ROOT/etc/os-release" 2>/dev/null && echo "${PRETTY_NAME:-}")
row "作業系統" "${os:-讀不到}"
row "架構／核心" "$(uname -m) / $(uname -r)"
row "CPU 核心數" "$(nproc 2>/dev/null || echo 讀不到)"
cuda="$ROOT/usr/local/cuda"
if [ -x "$cuda/bin/nvcc" ]; then
  row "CUDA" "$("$cuda/bin/nvcc" --version | tail -1)"
elif [ -r "$cuda/version.json" ]; then
  row "CUDA" "$(grep -m1 -o '"version" *: *"[^"]*"' "$cuda/version.json" | sed 's/.*"\([^"]*\)"$/\1/')"
else
  row "CUDA" "找不到 /usr/local/cuda"
fi

# ── 電源模式：影響速度，實測時要記下（docs/06 第 4 節）────
section "電源模式"
if has nvpmodel; then
  pm=$(nvpmodel -q 2>/dev/null | tr '\n' ' ')
  row "nvpmodel -q" "${pm:-沒有權限或讀不到（可請管理者執行 sudo nvpmodel -q）}"
else
  row "nvpmodel" "沒有這個指令"
fi

# ── B1：記憶體與磁碟 ───────────────────────────────
section "記憶體（GPU 與 CPU 共用）"
free -h | sed 's/^/    /'
mib() { awk -v k="$1" '$1 == k ":" {print int($2 / 1024) " MiB"}' "$ROOT/proc/meminfo" 2>/dev/null; }
mem=$(mib MemTotal)
avail=$(mib MemAvailable)
row "總共／目前可用" "${mem:-讀不到} / ${avail:-讀不到}"

section "磁碟"
# 不印掛載點：外接碟可能掛在 /media/<使用者名稱>/ 底下
root_fs=$(df -P / 2>/dev/null | awk 'NR==2 {print $1}')
root_avail=""
for d in / /opt /var/lib; do
  [ -d "$d" ] || continue
  info=$(df -Ph "$d" 2>/dev/null | awk -v r="$root_fs" 'NR==2 {print $4 "|" $2 "|" ($1 == r ? "和 / 同一個分割區" : "另一個分割區")}')
  IFS='|' read -r avail_d size_d where_d <<<"$info"
  [ "$d" = / ] && root_avail=$avail_d
  row "$d" "可用 ${avail_d:-?}／共 ${size_d:-?}（${where_d:-讀不到}）"
done

# ── Python：本 repo 要 MIN_PY 以上 ─────────────────
section "Python 與工具"
py_ok=""
py_ok_name=""
check_py() {  # $1 執行檔，$2 顯示的名稱
  local v
  v=$("$1" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null) || return
  if "$1" -c "import sys; sys.exit(sys.version_info < $MIN_PY_TUPLE)" 2>/dev/null; then
    row "$2" "$v（可用）"
    [ -n "$py_ok" ] || { py_ok=$1; py_ok_name=$2; }
  else
    row "$2" "$v（低於 $MIN_PY，本 repo 不能用）"
  fi
}
for py in python3 python3.11 python3.12 python3.13; do
  has "$py" && check_py "$py" "$py"
done
if has uv; then  # uv 裝的 Python 不在 PATH 上；不印路徑（含使用者名稱）
  uv_py=$(uv python find ">=$MIN_PY" 2>/dev/null) && [ -n "$uv_py" ] && check_py "$uv_py" "uv 找到的 Python"
fi
for t in git pip3 uv curl; do
  if has "$t"; then row "$t" "有"; else row "$t" "沒有"; fi
done
if [ -n "$py_ok" ]; then
  # Ubuntu 沒裝 pythonX.Y-venv 時 venv 模組還在，但缺 ensurepip，建 venv 才會失敗
  if "$py_ok" -c 'import ensurepip, venv' >/dev/null 2>&1; then row "$py_ok_name 建 venv" "可以"; else
    row "$py_ok_name 建 venv" "不行：缺 ensurepip（Ubuntu 要另裝 pythonX.Y-venv）"
    note "$py_ok_name 不能建 venv，要另外裝 venv 套件。"
  fi
else
  note "沒有 Python $MIN_PY 以上：要另外裝（docs/20「先知道的五件事」第 1 點），不要換掉系統內建的 python3。"
fi

# ── B2：上面還跑什麼 ───────────────────────────────
section "正在執行的服務（B2 的線索）"
if has systemctl; then
  for s in mosquitto docker ollama llama-server radar-agent nvargus-daemon; do
    state=$(systemctl is-active "$s" 2>/dev/null)
    [ "$state" = "active" ] && row "$s" "執行中"
  done
fi
if has ss; then
  ports=$(ss -Htln 2>/dev/null | awk '{n = split($4, a, ":"); print a[n]}' | sort -n | uniq | tr '\n' ' ')
  row "有在聽的 TCP 埠" "${ports:-讀不到}"
  case " $ports " in *" 1883 "*|*" 8883 "*) row "MQTT" "1883 或 8883 埠有服務（可能是 MQTT broker）" ;; esac
fi
# shellcheck disable=SC2009  # 要列出各程式的數量，pgrep 做不到
procs=$(ps -eo comm= 2>/dev/null | grep -iE 'mosquitto|emqx|ollama|llama|python|radar|docker|containerd' | sort | uniq -c | awk '{print $2 "×" $1}' | tr '\n' ' ')
row "相關的程式名稱" "${procs:-沒有看到}"
if has tegrastats; then
  ts=$(timeout 3 tegrastats --interval 1000 2>/dev/null | head -1)
  row "tegrastats（一次取樣）" "${ts:-讀不到}"
fi

# ── B5：外網 ───────────────────────────────────────
section "外網（B5）"
net_ok=""
if [ "$OFFLINE" = 1 ]; then
  row "外網" "略過（--offline）"
elif has curl; then
  for u in https://pypi.org https://github.com; do
    if curl -fsS -o /dev/null --max-time 8 "$u" 2>/dev/null; then row "$u" "連得到"; net_ok=1; else row "$u" "連不到"; fi
  done
  if [ -z "$net_ok" ]; then
    note "連不到 pypi.org 與 github.com：要用離線安裝（docs/20 第 2 步）。"
  else
    note "外網只測了能不能連上；現場若走 proxy 或登入頁，要實際 pip install 一次才算數。"
  fi
else
  row "外網" "沒有 curl，無法測"
fi

# ── 摘要 ───────────────────────────────────────────
section "摘要"
row "型號／記憶體" "${model:-不明} / ${mem:-讀不到}"
row "JetPack" "${jetpack:-看 L4T}"
row "根目錄可用空間" "${root_avail:-讀不到}（點雲預設在 Jetson 上留一個月，要一起算，A24；點雲存在哪要問雷達負責人）"
row "Python $MIN_PY 以上" "${py_ok_name:-沒有}"
if [ "${#NOTES[@]}" -gt 0 ]; then
  echo "要注意："
  for n in "${NOTES[@]}"; do echo "  - $n"; done
fi
echo
echo "把整段輸出貼到會議清單 B1、B2、B5 的回覆，或貼給 Claude 核對 docs/20。"
exit 0
