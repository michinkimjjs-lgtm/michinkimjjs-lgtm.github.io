"""Oracle Cloud 무료 ARM 서버(shorts-server)를 자리가 날 때까지 PC에서 자동으로 다시 만들어 보는 명령(파이썬).

사용(Windows 명령 프롬프트):  py oci_retry.py
- 처음 한 번: 오라클 화면에서 만든 'API 키'의 설정 미리보기 글을 붙여 넣으면, 다운로드 폴더의 .pem 열쇠 파일과 함께
  내 PC의 %USERPROFILE%\\.oci 폴더에 설정을 만든다(이 PC 밖으로 보내지 않음).
- 무료 범위 모양(VM.Standard.A1.Flex)만 시도: 4/24 → 2/12 → 1/6. 돈이 드는 설정은 쓰지 않는다.
- 이미 shorts-server가 있으면 아무것도 만들지 않는다(여러 번 실행해도 안전). 멈추려면 Ctrl+C.
"""

from __future__ import annotations

import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

NAME = "shorts-server"
SHAPE = "VM.Standard.A1.Flex"
SIZES = [(4, 24), (2, 12), (1, 6)]
GAP = 70      # 크기 사이 쉬는 초(오라클이 1분 안에 두 번 만들기를 "너무 자주"로 막음, 2026-11-27 기록 확인)
WAIT = 70     # 한 바퀴 뒤 쉬는 초
HOME = Path.home()
OCI_DIR = HOME / ".oci"
CONFIG = OCI_DIR / "config"
DOWNLOADS = HOME / "Downloads"


def say(text: str) -> None:
    print(f"[{datetime.now():%m-%d %H:%M:%S}] {text}", flush=True)


def newest(folder: Path, pattern: str) -> Path | None:
    files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def make_config(ask=input, downloads: Path = DOWNLOADS, oci_dir: Path = OCI_DIR) -> Path:
    """API 키 미리보기 글 + 다운로드 폴더의 .pem → %USERPROFILE%\\.oci\\config."""
    pem = newest(downloads, "*.pem")
    if pem is None:
        raise SystemExit("다운로드 폴더에서 API 키 파일(.pem)을 찾지 못했습니다. 오라클 화면에서 'Download private key'를 먼저 눌러 주세요.")
    print("오라클 화면의 'Configuration file preview' 글을 전부 복사해 여기에 붙여 넣고, 끝나면 빈 줄에서 Enter를 한 번 더 누르세요:")
    lines: list[str] = []
    while True:
        line = ask("")
        if not line.strip():
            if any(x.strip().startswith("tenancy=") for x in lines):
                break
            continue
        lines.append(line.rstrip())
    keys = {"user", "fingerprint", "tenancy", "region"}
    found = {x.split("=", 1)[0].strip(): x.split("=", 1)[1].strip() for x in lines if "=" in x}
    missing = keys - set(found)
    if missing:
        raise SystemExit(f"붙여 넣은 글에 {', '.join(sorted(missing))} 줄이 없습니다. 미리보기 글 전체를 다시 복사해 주세요.")
    oci_dir.mkdir(parents=True, exist_ok=True)
    key = oci_dir / "oci_api_key.pem"
    shutil.copyfile(pem, key)
    text = "[DEFAULT]\n" + "".join(f"{k}={found[k]}\n" for k in ("user", "fingerprint", "tenancy", "region"))
    text += f"key_file={key}\n"
    path = oci_dir / "config"
    path.write_text(text, encoding="utf-8")
    say(f"설정을 만들었습니다: {path}")
    return path


def ssh_public_key(ask=input, downloads: Path = DOWNLOADS) -> str:
    pub = newest(downloads, "ssh-key-*.pub")
    if pub is not None:
        text = pub.read_text(encoding="utf-8").strip()
        if text.startswith("ssh-"):
            say(f"서버 접속용 공개 키: {pub.name}")
            return text
    text = ask("ssh-key-....key.pub 파일을 메모장으로 열어 한 줄 전체를 붙여 넣으세요: ").strip()
    if not text.startswith("ssh-"):
        raise SystemExit("ssh- 로 시작하는 공개 키가 아닙니다.")
    return text


def is_no_capacity(exc) -> bool:
    msg = f"{getattr(exc, 'code', '')} {getattr(exc, 'message', '')}".lower()
    return "capacity" in msg


def find_subnet(network, t: str) -> str:
    import oci

    subnets = oci.pagination.list_call_get_all_results(network.list_subnets, compartment_id=t).data
    for s in subnets:
        if not s.prohibit_public_ip_on_vnic and s.lifecycle_state == "AVAILABLE":
            return s.id
    say("공개 네트워크가 없어 새로 만듭니다.")
    m = oci.core.models
    vcn = network.create_vcn(m.CreateVcnDetails(compartment_id=t, cidr_blocks=["10.0.0.0/16"],
                                                display_name="shorts-vcn")).data
    oci.wait_until(network, network.get_vcn(vcn.id), "lifecycle_state", "AVAILABLE")
    igw = network.create_internet_gateway(m.CreateInternetGatewayDetails(
        compartment_id=t, vcn_id=vcn.id, is_enabled=True, display_name="shorts-igw")).data
    oci.wait_until(network, network.get_internet_gateway(igw.id), "lifecycle_state", "AVAILABLE")
    network.update_route_table(vcn.default_route_table_id, m.UpdateRouteTableDetails(route_rules=[
        m.RouteRule(destination="0.0.0.0/0", destination_type="CIDR_BLOCK", network_entity_id=igw.id)]))
    sub = network.create_subnet(m.CreateSubnetDetails(compartment_id=t, vcn_id=vcn.id, cidr_block="10.0.0.0/24",
                                                      display_name="shorts-subnet")).data
    oci.wait_until(network, network.get_subnet(sub.id), "lifecycle_state", "AVAILABLE")
    return sub.id


def find_image(compute, t: str) -> str:
    import oci

    images = oci.pagination.list_call_get_all_results(
        compute.list_images, compartment_id=t, operating_system="Canonical Ubuntu",
        operating_system_version="24.04", shape=SHAPE, sort_by="TIMECREATED", sort_order="DESC",
        lifecycle_state="AVAILABLE").data
    for img in images:
        if "Minimal" not in (img.display_name or ""):
            return img.id
    raise SystemExit("Ubuntu 24.04 이미지를 찾지 못했습니다.")


def already_exists(compute, t: str) -> bool:
    import oci

    items = oci.pagination.list_call_get_all_results(compute.list_instances, compartment_id=t,
                                                     display_name=NAME).data
    return any(i.lifecycle_state not in ("TERMINATED", "TERMINATING") for i in items)


def launch_until_success(compute, network, t: str, ad: str, image: str, subnet: str, pub: str,
                         sleep=time.sleep, max_rounds: int | None = None) -> str:
    """자리가 날 때까지 시도. 성공하면 서버 ID."""
    import oci

    m = oci.core.models
    tries, rounds = 0, 0
    while max_rounds is None or rounds < max_rounds:
        rounds += 1
        for ocpus, mem in SIZES:
            tries += 1
            details = m.LaunchInstanceDetails(
                compartment_id=t, availability_domain=ad, display_name=NAME, shape=SHAPE,
                shape_config=m.LaunchInstanceShapeConfigDetails(ocpus=float(ocpus), memory_in_gbs=float(mem)),
                source_details=m.InstanceSourceViaImageDetails(image_id=image),
                create_vnic_details=m.CreateVnicDetails(subnet_id=subnet, assign_public_ip=True),
                metadata={"ssh_authorized_keys": pub})
            try:
                inst = compute.launch_instance(details).data
            except oci.exceptions.ServiceError as exc:
                if is_no_capacity(exc):
                    say(f"{tries}번째: {ocpus}/{mem} 자리 없음")
                elif exc.status == 429:
                    say(f"{tries}번째: 너무 자주 시도해 잠깐 쉽니다")
                    sleep(120)
                elif exc.code == "LimitExceeded":
                    say(f"{tries}번째: {ocpus}/{mem}는 계정 한도 초과(다음 크기로)")
                else:
                    raise SystemExit(f"예상 못 한 오류라 멈춥니다(캡처해서 보내 주세요): {exc.status} {exc.code} {exc.message}")
            except oci.exceptions.RequestException as exc:   # 인터넷 끊김 등: 잠시 뒤 다시
                say(f"{tries}번째: 연결 문제({type(exc).__name__}) — 잠시 뒤 다시")
            else:
                say(f"성공! {ocpus} OCPU / {mem}GB 서버를 만들고 있습니다.")
                return inst.id
            sleep(GAP)
        sleep(WAIT)
    return ""


def public_ip(compute, network, t: str, instance_id: str) -> str:
    import oci

    try:
        oci.wait_until(compute, compute.get_instance(instance_id), "lifecycle_state", "RUNNING",
                       max_wait_seconds=900)
        att = compute.list_vnic_attachments(compartment_id=t, instance_id=instance_id).data
        return network.get_vnic(att[0].vnic_id).data.public_ip or "" if att else ""
    except Exception:
        return ""


def main() -> int:
    try:
        import oci
    except ImportError:
        print("먼저 이 명령을 넣으세요:  py -m pip install oci")
        return 1
    if not CONFIG.is_file():
        make_config()
    config = oci.config.from_file(str(CONFIG))
    oci.config.validate_config(config)
    t = config["tenancy"]
    identity = oci.identity.IdentityClient(config)
    compute = oci.core.ComputeClient(config)
    network = oci.core.VirtualNetworkClient(config)
    try:
        identity.get_user(config["user"])
    except oci.exceptions.ServiceError as exc:
        print(f"오라클 접속이 거절됐습니다({exc.status} {exc.code}). API 키를 추가할 때 'Add'를 눌렀는지 확인하세요.")
        return 1
    say("오라클 접속 확인")
    if already_exists(compute, t):
        say(f"이미 {NAME} 서버가 있습니다. 새로 만들지 않습니다(오라클 화면의 인스턴스 목록을 확인하세요).")
        return 0
    pub = ssh_public_key()
    subnet = find_subnet(network, t)
    image = find_image(compute, t)
    ad = identity.list_availability_domains(t).data[0].name
    say("준비 완료. 자리가 날 때까지 계속 시도합니다(멈추려면 Ctrl+C). PC가 절전 모드로 들어가지 않게 해 두세요.")
    instance_id = launch_until_success(compute, network, t, ad, image, subnet, pub)
    ip = public_ip(compute, network, t, instance_id)
    say(f"서버가 켜졌습니다. 공개 IP: {ip or '(오라클 화면에서 확인)'}")
    print("\a")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n멈췄습니다. 다시 시작하려면 같은 명령을 넣으세요.")
