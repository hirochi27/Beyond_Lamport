## 起動方法
シーケンサの起動
```
cd ~/Beyond_lamport
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```
clientの起動
```
cd ~/Beyond_lamport
.venv/bin/python app/probe_client.py \
  --sequencer http://127.0.0.1:8000 \
  --client-id local-test \
  --samples 5 \
  --interval 0.1
```


プローブ
    最初の数回は外れ値がでる
    ```
    uvicorn / FastAPI の初回処理
Python の初回 import 後のキャッシュ
HTTP 接続の初回確立
OS のスケジューリング
WSL の初回アクセス
CPU が省電力状態から起きる
DNS やネットワークスタックの初回処理
みたいな「測定の準備運動」的な遅延が入りやすいからです。
    ```
    だから念のため最初の5回のデータは捨てて、その後100回計測してみる

他にもプローブの外れ値だけ除外したほうがよさそう
たまにめちゃくちゃな外れ値おる
```
1/100 offset=298.932us rtt=4572.434us average=298.932us stddev=0.000us
2/100 offset=183.806us rtt=5032.779us average=241.369us stddev=81.406us
3/100 offset=466.842us rtt=5027.434us average=316.527us stddev=142.336us
4/100 offset=1040.069us rtt=5333.403us average=497.412us stddev=379.980us
5/100 offset=119.348us rtt=4030.310us average=421.799us stddev=369.966us
6/100 offset=-1473.892us rtt=7829.111us average=105.851us stddev=841.689us
7/100 offset=6923.274us rtt=18002.464us average=1079.768us stddev=2688.862us
8/100 offset=-384.863us rtt=13658.697us average=896.689us stddev=2542.689us
9/100 offset=360.654us rtt=3248.363us average=837.130us stddev=2385.170us
10/100 offset=373.252us rtt=4302.009us average=790.742us stddev=2253.539us
11/100 offset=402.583us rtt=4089.057us average=755.455us stddev=2141.096us
12/100 offset=441.308us rtt=4186.673us average=729.276us stddev=2043.468us
13/100 offset=219.236us rtt=3765.191us average=690.042us stddev=1961.579us
14/100 offset=297.314us rtt=4090.844us average=661.990us stddev=1887.545us
15/100 offset=167.459us rtt=4588.824us average=629.021us stddev=1823.360us
16/100 offset=277.910us rtt=4325.271us average=607.077us stddev=1763.719us
17/100 offset=322.533us rtt=3528.709us average=590.339us stddev=1709.107us
18/100 offset=115.481us rtt=4571.830us average=563.958us stddev=1661.851us
19/100 offset=62.705us rtt=3941.380us average=537.576us stddev=1619.117us
20/100 offset=5472.863us rtt=15362.039us average=784.341us stddev=1923.907us
21/100 offset=315.236us rtt=3521.209us average=762.002us stddev=1877.985us
22/100 offset=203.915us rtt=3648.284us average=736.635us stddev=1836.584us
23/100 offset=237.829us rtt=3468.904us average=714.947us stddev=1797.370us
24/100 offset=28.865us rtt=3892.142us average=686.361us stddev=1763.432us
25/100 offset=39.861us rtt=3916.363us average=660.501us stddev=1731.138us
26/100 offset=-9.438us rtt=3622.799us average=634.734us stddev=1701.243us
27/100 offset=182.023us rtt=4108.038us average=617.967us stddev=1670.480us
28/100 offset=189.823us rtt=5173.529us average=602.676us stddev=1641.249us
29/100 offset=220.818us rtt=3433.640us average=589.508us stddev=1613.233us
30/100 offset=177.631us rtt=3385.418us average=575.779us stddev=1586.958us
31/100 offset=124.453us rtt=3494.166us average=561.220us stddev=1562.388us
32/100 offset=128.278us rtt=3948.150us average=547.691us stddev=1538.886us
33/100 offset=354.020us rtt=3791.998us average=541.822us stddev=1515.026us
34/100 offset=204.466us rtt=3611.813us average=531.900us stddev=1493.015us
35/100 offset=217.072us rtt=2856.695us average=522.905us stddev=1471.858us
36/100 offset=234.311us rtt=4321.785us average=514.888us stddev=1451.476us
37/100 offset=41.522us rtt=3265.702us average=502.094us stddev=1433.289us
38/100 offset=149.954us rtt=3481.747us average=492.828us stddev=1414.941us
39/100 offset=331.318us rtt=3322.960us average=488.686us stddev=1396.439us
40/100 offset=511.654us rtt=4173.498us average=489.261us stddev=1378.424us
41/100 offset=278.452us rtt=4402.987us average=484.119us stddev=1361.483us
42/100 offset=604.124us rtt=4480.793us average=486.976us stddev=1344.905us
43/100 offset=406.090us rtt=3418.732us average=485.095us stddev=1328.855us
44/100 offset=490.406us rtt=4148.746us average=485.216us stddev=1313.312us
45/100 offset=414.370us rtt=3964.544us average=483.641us stddev=1298.345us
46/100 offset=317.763us rtt=3773.880us average=480.035us stddev=1284.071us
47/100 offset=204.745us rtt=4144.262us average=474.178us stddev=1270.672us
48/100 offset=354.595us rtt=3968.901us average=471.687us stddev=1257.200us
49/100 offset=643.800us rtt=4552.954us average=475.199us stddev=1244.278us
50/100 offset=243.183us rtt=4492.669us average=470.559us stddev=1231.953us
51/100 offset=341.644us rtt=4460.514us average=468.031us stddev=1219.705us
52/100 offset=176.648us rtt=4325.800us average=462.428us stddev=1208.363us
53/100 offset=614.268us rtt=4417.522us average=465.293us stddev=1196.870us
54/100 offset=310.952us rtt=4172.853us average=462.434us stddev=1185.711us
55/100 offset=71.880us rtt=4013.051us average=455.333us stddev=1175.861us
56/100 offset=425.646us rtt=4124.522us average=454.803us stddev=1165.129us
57/100 offset=376.802us rtt=4075.205us average=453.435us stddev=1154.725us
58/100 offset=308.889us rtt=4185.168us average=450.943us stddev=1144.709us
59/100 offset=310.460us rtt=3962.323us average=448.562us stddev=1134.945us
60/100 offset=279.410us rtt=4470.502us average=445.742us stddev=1125.497us
61/100 offset=378.463us rtt=4641.568us average=444.640us stddev=1116.112us
62/100 offset=64.704us rtt=4152.128us average=438.512us stddev=1107.977us
63/100 offset=49.945us rtt=3953.455us average=432.344us stddev=1100.095us
64/100 offset=294.472us rtt=4075.364us average=430.190us stddev=1091.465us
65/100 offset=374.210us rtt=3786.090us average=429.328us stddev=1082.927us
66/100 offset=-25.421us rtt=4543.159us average=422.438us stddev=1076.022us
67/100 offset=342.024us rtt=4573.804us average=421.238us stddev=1067.884us
68/100 offset=400.920us rtt=3874.454us average=420.939us stddev=1059.888us
69/100 offset=467.060us rtt=4002.811us average=421.608us stddev=1052.080us
70/100 offset=281.856us rtt=3988.753us average=419.611us stddev=1044.562us
71/100 offset=279.212us rtt=4597.485us average=417.634us stddev=1037.208us
72/100 offset=491.543us rtt=3839.756us average=418.660us stddev=1029.915us
73/100 offset=313.767us rtt=3565.156us average=417.223us stddev=1022.811us
74/100 offset=125.753us rtt=4485.149us average=413.285us stddev=1016.346us
75/100 offset=209.579us rtt=4407.674us average=410.568us stddev=1009.730us
76/100 offset=152.701us rtt=3984.426us average=407.175us stddev=1003.412us
77/100 offset=189.333us rtt=3889.972us average=404.346us stddev=997.098us
78/100 offset=300.118us rtt=4362.465us average=403.010us stddev=990.672us
79/100 offset=353.236us rtt=4320.140us average=402.380us stddev=984.317us
80/100 offset=327.783us rtt=5159.854us average=401.448us stddev=978.103us
81/100 offset=358.849us rtt=4472.373us average=400.922us stddev=971.982us
82/100 offset=784.711us rtt=5442.445us average=405.602us stddev=966.893us
83/100 offset=392.055us rtt=4765.760us average=405.439us stddev=960.980us
84/100 offset=447.733us rtt=4767.469us average=405.942us stddev=955.185us
85/100 offset=567.749us rtt=5129.850us average=407.846us stddev=949.644us
86/100 offset=427.137us rtt=4744.646us average=408.070us stddev=944.044us
87/100 offset=611.838us rtt=5775.028us average=410.412us stddev=938.793us
88/100 offset=401.450us rtt=6701.043us average=410.311us stddev=933.383us
89/100 offset=1884.378us rtt=8641.937us average=426.873us stddev=941.126us
90/100 offset=178.935us rtt=5213.510us average=424.118us stddev=936.189us
91/100 offset=586.284us rtt=5309.079us average=425.900us stddev=931.128us
92/100 offset=690.974us rtt=5546.965us average=428.782us stddev=926.410us
93/100 offset=294.072us rtt=5305.921us average=427.333us stddev=921.468us
94/100 offset=378.610us rtt=4726.080us average=426.815us stddev=916.514us
95/100 offset=211.284us rtt=4532.075us average=424.546us stddev=911.894us
96/100 offset=147.981us rtt=4612.862us average=421.665us stddev=907.521us
97/100 offset=224.087us rtt=4719.156us average=419.628us stddev=903.005us
98/100 offset=323.453us rtt=4258.065us average=418.647us stddev=898.391us
99/100 offset=193.120us rtt=4392.257us average=416.369us stddev=894.082us
100/100 offset=347.718us rtt=4574.118us average=415.682us stddev=889.582us
{
  "client_id": "local-test",
  "sample_count": 100,
  "average_offset_seconds": 0.00041568225,
  "stddev_offset_seconds": 0.0008895819570797742,
  "min_offset_seconds": -0.0014738925,
  "max_offset_seconds": 0.006923274,
  "uncertainty_seconds": 0.0017791639141595485
}
```