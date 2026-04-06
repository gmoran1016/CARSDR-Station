import subprocess, signal, time, struct, math, sys

def rms(data):
    count = len(data) // 2
    samples = struct.unpack('<{}h'.format(count), data[:count*2])
    return math.sqrt(sum(s*s for s in samples) / count)

print("Starting rtl_fm...")
proc = subprocess.Popen(
    ['rtl_fm','-f','100300000','-M','fm','-s','200000',
     '-r','48000','-E','deemp','-F','9','-l','0','-g','40','-p','0'],
    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
)
print("PID:", proc.pid)
time.sleep(0.5)
print("Reading chunk...")
data = proc.stdout.read(4096)
print("Got", len(data), "bytes, RMS =", round(rms(data), 1) if data else 0)
proc.send_signal(signal.SIGINT)
proc.wait()
print("Done")
