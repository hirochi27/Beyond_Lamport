import argparse
import json
import time
from argparse import Namespace
from urllib import request

###クライアント側の操作/プローブの計測


#シーケンサにgetを送る
def get_json(url: str) -> dict:
    with request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


#timestamp＊4を/api/clock-offset-samples に POST する。？？
def post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


#入力された条件を読む
def parse_args() -> Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sequencer", default="http://127.0.0.1:8000")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--samples", type=int, default=100) #100回プローブ
    parser.add_argument("--warmup", type=int, default=5) #最初のデータは使わない
    parser.add_argument("--interval", type=float, default=0.1)
    return parser.parse_args()


def normalize_base_url(sequencer_url: str) -> str:
    return sequencer_url.rstrip("/")


#プローブのtime stampの管理
#client_send_time_ns : クライアント側プローブの送信時間（timestamp）
#client_receive_time_ns　：　クライアント側プローブの受信時間(timestamp)
def collect_warmup_probe(base_url: str) -> None:
    #分布には登録せず、初回通信の遅延だけ逃がす
    get_json(f"{base_url}/api/clock-probe")


#シーケンサとクライアントのタイムスタンプを合体
def collect_probe(base_url: str, client_id: str) -> dict:
    #送信時刻をのせて、probeを送る
    client_send_time_ns = time.time_ns()
    probe = get_json(f"{base_url}/api/clock-probe")
    #受け取った時間を記録
    client_receive_time_ns = time.time_ns()
    #辞書（sample）にまとめる
    sample = {
        "client_id": client_id,
        "client_send_time_ns": client_send_time_ns,
        "sequencer_receive_time_ns": probe["sequencer_receive_time_ns"],
        "sequencer_send_time_ns": probe["sequencer_send_time_ns"],
        "client_receive_time_ns": client_receive_time_ns,
    }
    #できたoffsetをシーケンサに送信
    return post_json(f"{base_url}/api/clock-offset-samples", sample)


def run_warmup(base_url: str, warmup_count: int, interval: float) -> None:
    #最初の数回は分布に入れず、初回通信のノイズを捨てる（5回）
    for warmup_index in range(warmup_count):
        #/api/clock-probe に GET して、初回通信のノイズを逃がす。
        collect_warmup_probe(base_url)
        print(f"warmup {warmup_index + 1}/{warmup_count}")
        time.sleep(interval)


def print_probe_response(index: int, sample_count: int, response: dict) -> None:
    sample = response["sample"]
    distribution = response["distribution"]
    print(
        f"{index + 1}/{sample_count} " #何回目のプローブか
        f"offset={sample['offset_seconds'] * 1_000_000:.3f}us " #その回で計算したclock offset(マイクロ秒)
        f"rtt={sample['round_trip_delay_seconds'] * 1_000_000:.3f}us " #RTT（マイクロ秒）
        f"average={distribution['average_offset_seconds'] * 1_000_000:.3f}us " #offsetの平均値
        f"stddev={distribution['stddev_offset_seconds'] * 1_000_000:.3f}us " #標準偏差(広がり)（小さいほどばらつきが少ない）
        f"median_rtt={distribution['median_rtt_seconds'] * 1_000_000:.3f}us "
        f"rejected={distribution['rejected_sample_count']}"
    )


#指定回数分プローブを繰り返す
def collect_samples(
    base_url: str,
    client_id: str,
    sample_count: int,
    interval: float,
) -> dict | None:
    last_response = None #最後のプローブ結果を一時保存するための変数
    #指定された回数分繰り返す
    for index in range(sample_count):
        response = collect_probe(base_url, client_id) #シーケンサ側のtimestampと合体
        last_response = response
        print_probe_response(index, sample_count, response)
        time.sleep(interval)

    return last_response


def print_final_distribution(response: dict | None) -> None:
    #もし最後のプローブが終わっていたら、print
    if response is not None:
        print(json.dumps(response["distribution"], indent=2))


def main() -> None:
    #入力された値を読む
    args = parse_args()
    #########################################
    # --sequencer  接続先の sequencer API   #
    # --client-id  クライアントの名前    #
    # --samples    warmup後にprobeを何回打つか #
    # --warmup     分布に入れず捨てるprobe回数 #
    # --interval   probe の間隔 秒          #
    #########################################

    #シーケンサ―URLの末尾の/を削る
    base_url = normalize_base_url(args.sequencer)
    #計算に入れない最初の5回のプローブ
    run_warmup(base_url, args.warmup, args.interval)
    #プローブの計測
    last_response = collect_samples(
        base_url,
        args.client_id,
        args.samples,
        args.interval,
    )
    #最後の分布を表示
    print_final_distribution(last_response)


if __name__ == "__main__":
    main()
