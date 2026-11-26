#!/usr/bin/env bash
# Oracle Cloud 무료 ARM 서버(shorts-server)를 자리가 날 때까지 자동으로 다시 만들어 보는 명령.
# Oracle 웹 화면의 Cloud Shell에서 실행: bash oci-retry.sh
# - 무료 범위 모양(VM.Standard.A1.Flex)만 시도: 4/24 → 2/12 → 1/6 순서. 돈이 드는 설정은 쓰지 않음.
# - 이미 shorts-server가 있으면 아무것도 만들지 않음(여러 번 실행해도 안전).
# - 비밀값을 저장하거나 밖으로 보내지 않음. 공개 키(.pub)만 받음.
set -uo pipefail
NAME="shorts-server"
WAIT=60          # 한 바퀴(3가지 크기) 뒤 쉬는 초
GAP=30           # 크기 사이 쉬는 초(너무 붙여 시도하면 오라클이 "너무 자주"로 막음)
SHAPES=("4 24" "2 12" "1 6")

say() { echo "[$(date '+%m-%d %H:%M:%S')] $*"; }

# ---- 1. 계정(테넌시) 번호 찾기 ----
T="${OCI_TENANCY:-}"
if [ -z "$T" ] && [ -n "${OCI_CLI_CONFIG_FILE:-}" ] && [ -f "$OCI_CLI_CONFIG_FILE" ]; then
  T=$(grep -m1 '^tenancy' "$OCI_CLI_CONFIG_FILE" | cut -d= -f2 | tr -d ' ')
fi
if [ -z "$T" ] && [ -n "${OCI_CS_USER_OCID:-}" ]; then
  T=$(oci iam user get --user-id "$OCI_CS_USER_OCID" --query 'data."compartment-id"' --raw-output 2>/dev/null)
fi
if [ -z "$T" ]; then
  echo "계정 번호(Tenancy OCID)를 찾지 못했습니다."
  echo "오른쪽 위 사람 모양 → Tenancy → OCID 옆 'Copy'를 눌러 복사한 뒤 붙여 넣으세요:"
  read -r T
fi
say "계정 확인: ${T:0:30}..."

# ---- 2. 공개 키(.pub) ----
KEYFILE="$HOME/shorts-server.pub"
if [ ! -s "$KEYFILE" ]; then
  echo "PC에 받아 둔 ssh-key-....key.pub 파일을 메모장으로 열어, 한 줄 전체(ssh-rsa 로 시작)를 복사해 붙여 넣고 Enter:"
  read -r PUB
  case "$PUB" in ssh-*) printf '%s\n' "$PUB" > "$KEYFILE" ;; *) echo "ssh- 로 시작하는 공개 키가 아닙니다. 다시 실행하세요."; exit 1 ;; esac
fi

# ---- 3. 이미 있나 ----
exists() {
  oci compute instance list -c "$T" --display-name "$NAME" \
    --query "length(data[?\"lifecycle-state\"!='TERMINATED' && \"lifecycle-state\"!='TERMINATING'])" --raw-output 2>/dev/null
}
if [ "$(exists)" != "0" ] && [ -n "$(exists)" ]; then
  say "이미 $NAME 서버가 있습니다. 새로 만들지 않습니다(Oracle 화면의 인스턴스 목록을 확인하세요)."
  exit 0
fi

# ---- 4. 네트워크(공개 서브넷) ----
SUBNET=$(oci network subnet list -c "$T" --all \
  --query "data[?\"prohibit-public-ip-on-vnic\"==\`false\` && \"lifecycle-state\"=='AVAILABLE'] | [0].id" --raw-output 2>/dev/null)
if [ -z "$SUBNET" ] || [ "$SUBNET" = "null" ]; then
  say "공개 네트워크가 없어 새로 만듭니다."
  VCN=$(oci network vcn create -c "$T" --cidr-blocks '["10.0.0.0/16"]' --display-name shorts-vcn \
        --wait-for-state AVAILABLE --query data.id --raw-output) || { say "네트워크를 만들지 못했습니다."; exit 1; }
  IGW=$(oci network internet-gateway create -c "$T" --vcn-id "$VCN" --is-enabled true --display-name shorts-igw \
        --wait-for-state AVAILABLE --query data.id --raw-output) || exit 1
  RT=$(oci network vcn get --vcn-id "$VCN" --query 'data."default-route-table-id"' --raw-output)
  oci network route-table update --rt-id "$RT" --force \
      --route-rules "[{\"destination\":\"0.0.0.0/0\",\"destinationType\":\"CIDR_BLOCK\",\"networkEntityId\":\"$IGW\"}]" >/dev/null || exit 1
  SUBNET=$(oci network subnet create -c "$T" --vcn-id "$VCN" --cidr-block 10.0.0.0/24 --display-name shorts-subnet \
        --wait-for-state AVAILABLE --query data.id --raw-output) || exit 1
fi
say "네트워크 확인: ${SUBNET:0:30}..."

# ---- 5. 이미지(Ubuntu 24.04, ARM, Minimal 아님)·가용성 도메인 ----
IMG=$(oci compute image list -c "$T" --operating-system "Canonical Ubuntu" --operating-system-version "24.04" \
      --shape "VM.Standard.A1.Flex" --lifecycle-state AVAILABLE --sort-by TIMECREATED --sort-order DESC --all \
      --query "data[?!contains(\"display-name\", 'Minimal')] | [0].id" --raw-output)
AD=$(oci iam availability-domain list -c "$T" --query 'data[0].name' --raw-output)
if [ -z "$IMG" ] || [ "$IMG" = "null" ] || [ -z "$AD" ]; then say "Ubuntu 24.04 이미지나 지역 정보를 찾지 못했습니다."; exit 1; fi
say "이미지·지역 확인 완료. 자리가 날 때까지 계속 시도합니다(멈추려면 Ctrl+C)."

# ---- 6. 자리가 날 때까지 ----
try=0
while true; do
  for s in "${SHAPES[@]}"; do
    set -- $s
    try=$((try + 1))
    OUT=$(oci compute instance launch -c "$T" --availability-domain "$AD" --display-name "$NAME" \
          --shape "VM.Standard.A1.Flex" --shape-config "{\"ocpus\":$1,\"memoryInGBs\":$2}" \
          --image-id "$IMG" --subnet-id "$SUBNET" --assign-public-ip true \
          --ssh-authorized-keys-file "$KEYFILE" --query data.id --raw-output 2>&1)
    if [ $? -eq 0 ] && [[ "$OUT" == ocid1.instance* ]]; then
      say "성공! ${1} OCPU / ${2}GB 서버를 만들고 있습니다. 켜질 때까지 기다립니다..."
      oci compute instance get --instance-id "$OUT" --wait-for-state RUNNING --max-wait-seconds 900 >/dev/null 2>&1
      IP=$(oci compute instance list-vnics --instance-id "$OUT" --query 'data[0]."public-ip"' --raw-output 2>/dev/null)
      say "서버가 켜졌습니다. 공개 IP: ${IP:-'(Oracle 화면에서 확인)'}"
      exit 0
    fi
    case "$OUT" in
      *"Out of host capacity"*|*"Out of capacity"*|*OutOfHostCapacity*|*InternalError*)
        say "${try}번째: ${1}/${2} 자리 없음" ;;
      *TooManyRequests*|*429*)
        say "${try}번째: 너무 자주 시도해 잠깐 쉽니다"; sleep 120 ;;
      *LimitExceeded*|*"limit"*)
        say "${try}번째: ${1}/${2}는 계정 한도 초과(다음 크기로)" ;;
      *)
        say "예상 못 한 오류라 멈춥니다. 아래 내용을 캡처해서 보내 주세요:"; echo "$OUT" | tail -5; exit 1 ;;
    esac
    sleep "$GAP"
  done
  sleep "$WAIT"
done
