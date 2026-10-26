#!/usr/bin/env bash
# 確認 Jetson 的規格與現況，回答會議清單 B1（型號、記憶體、JetPack、磁碟）、B5（外網），
# 並列出 B2（上面還跑什麼）的線索。見 docs/20_Jetson部署流程.md 第 0 步。
#
# 只讀：不安裝、不改設定、不用 sudo（需要 root 的項目改印「沒有權限」）。
# 不印出 IP、MAC、主機名稱與使用者名稱，輸出可以直接貼到 issue 或對話裡。
#
# 用法：bash scripts/jetson_check.sh            # 含外網測試（連 pypi.org、github.com）
#       bash scripts/jetson_check.sh --offline  # 不連外
# 結束碼一律是 0：這是盤點，判斷寫在最後的「摘要」。

set -u
export LC_ALL=C

OFFLINE=0
case "${1:-}" in
  --offline) OFFLINE=1 ;;
  "") ;;
  *) echo "用法：bash $0 [--offline]" >&2; exit 2 ;;
esac

NOTES=()
note() { NOTES+=("$1"); }
has() { command -v "$1" >/dev/null 2>&1; }
section() { printf '\n## %s\n' "$1"; }
row() { printf -- '- %s：%s\n' "$1" "$2"; }

echo "# Jetson 盤點（scripts/jetson_check.sh，$(date -u +%Y-%m-%dT%H:%MZ)）"

# ── B1：型號與 JetPack ─────────────────────────────
section "型號與系統（B1）"
model=""
[ -r /proc/device-tree/model ] && model=$(tr -d '\0' </proc/device-tree/model)
row "型號" "${model:-讀不到（/proc/device-tree/model）}"
if [ -r /etc/nv_tegra_release ]; then
  row "L4T" "$(head -1 /etc/nv_tegra_release)"
else
  row "L4T" "沒有 /etc/nv_tegra_release（可能不是 Jetson）"
  note "不是 Jetson，或 JetPack 沒裝好：下面的結果只能參考。"
fi
jetpack=$(dpkg-query -W -f='${Version}' nvidia-jetpack 2>/dev/null || true)
row "JetPack（nvidia-jetpack 套件）" "${jetpack:-沒有安裝這個套件（不一定代表沒有 JetPack，看上面的 L4T）}"
# shellcheck disable=SC1091  # 執行時才讀系統檔
os=$( . /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-}")
row "作業系統" "${os:-讀不到}"
row "架構／核心" "$(uname -m) / $(uname -r)"
row "CPU 核心數" "$(nproc 2>/dev/null || echo 讀不到)"
if [ -x /usr/local/cuda/bin/nvcc ]; then
  row "CUDA" "$(/usr/local/cuda/bin/nvcc --version | tail -1)"
elif [ -r /usr/local/cuda/version.json ]; then
  row "CUDA" "$(grep -m1 -o '"version" *: *"[^"]*"' /usr/local/cuda/version.json)"
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
mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
avail_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
row "總共／目前可用" "$((mem_kb / 1024)) MiB / $((avail_kb / 1024)) MiB"

section "磁碟"
for d in / /opt /var/lib; do
  [ -d "$d" ] || continue
  row "$d" "$(df -Ph "$d" 2>/dev/null | awk 'NR==2 {print "可用 " $4 "／共 " $2 "（掛在 " $6 "）"}')"
done
root_avail_gb=$(df -Pk / | awk 'NR==2 {print int($4 / 1048576)}')

# ── Python：本 repo 要 3.11 以上（pyproject.toml）──────
section "Python 與工具"
py_ok=""
for py in python3 python3.11 python3.12 python3.13; do
  has "$py" || continue
  v=$("$py" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null) || continue
  if "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    row "$py" "$v（可用）"
    py_ok=${py_ok:-$py}
  else
    row "$py" "$v（低於 3.11，本 repo 不能用）"
  fi
done
for t in git pip3 uv curl; do
  if has "$t"; then row "$t" "有"; else row "$t" "沒有"; fi
done
if [ -n "$py_ok" ]; then
  if "$py_ok" -m venv --help >/dev/null 2>&1; then row "$py_ok -m venv" "可用"; else
    row "$py_ok -m venv" "不能用（Ubuntu 要另裝 pythonX.Y-venv）"
    note "$py_ok 沒有 venv 模組，要另外裝 venv 套件。"
  fi
else
  note "沒有 Python 3.11 以上：要另外裝（docs/20「先知道的五件事」第 1 點），不要換掉系統內建的 python3。"
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
    if curl -sS -o /dev/null --max-time 8 "$u" 2>/dev/null; then row "$u" "連得到"; net_ok=1; else row "$u" "連不到"; fi
  done
  [ -z "$net_ok" ] && note "連不到 pypi.org 與 github.com：要用離線安裝（docs/20 第 2 步）。"
else
  row "外網" "沒有 curl，無法測"
fi

# ── 摘要 ───────────────────────────────────────────
section "摘要"
row "型號／記憶體" "${model:-不明} / $((mem_kb / 1024)) MiB"
row "JetPack" "${jetpack:-看 L4T}"
row "根目錄可用空間" "${root_avail_gb} GiB（點雲預設在 Jetson 上留一個月，要一起算，A24）"
row "Python 3.11 以上" "${py_ok:-沒有}"
if [ "${#NOTES[@]}" -gt 0 ]; then
  echo "要注意："
  for n in "${NOTES[@]}"; do echo "  - $n"; done
fi
echo
echo "把整段輸出貼到會議清單 B1、B2、B5 的回覆，或貼給 Claude 核對 docs/20。"
exit 0
