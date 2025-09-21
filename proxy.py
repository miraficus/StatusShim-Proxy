import asyncio
import json

POSEIDON_HOST = 'miraficus.cz'
POSEIDON_PORT = 25575
PROXY_PORT = 25585

def read_varint(data):
    num = 0
    for i in range(len(data)):
        byte = data[i]
        num |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return num, i + 1
    raise ValueError("VarInt too long")

def write_varint(value):
    out = bytearray()
    while True:
        temp = value & 0x7F
        value >>= 7
        if value:
            out.append(temp | 0x80)
        else:
            out.append(temp)
            break
    return bytes(out)

async def read_packet(reader):
    try:
        prefix = await reader.read(1)
        buffer = prefix + await reader.read(4)
        length, len_len = read_varint(buffer)
        packet = await reader.readexactly(length)
        return packet
    except Exception as e:
        raise Exception(f"Failed to read packet: {e}")

async def handle_client(reader, writer):
    peer = writer.get_extra_info('peername')
    print(f"[+] Connection from {peer}")

    try:
        peek = await reader.read(1)
        if not peek:
            raise Exception("Empty connection")

        if peek == b'\xfe':  # Legacy ping
            print("[!] Legacy ping detected")
            reader_p, writer_p = await asyncio.open_connection(POSEIDON_HOST, POSEIDON_PORT)
            writer_p.write(peek)
            await writer_p.drain()
            response = await reader_p.read(1024)
            writer_p.close()
            await writer_p.wait_closed()
            writer.write(response)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            print("[✓] Responded to legacy ping")
            return

        # Modern ping: read handshake
        buffer = peek + await reader.read(4)
        length, len_len = read_varint(buffer)
        handshake = await reader.readexactly(length)
        packet_id, id_len = read_varint(handshake)
        payload = handshake[id_len:]

        if packet_id == 0x00 and payload[-1] == 1:  # Status intent
            print("[!] Modern status ping detected")

            # Read status request
            status_packet = await read_packet(reader)
            status_id, _ = read_varint(status_packet)
            if status_id != 0x01:
                raise Exception("Unexpected packet after handshake")

            # Send legacy ping to Poseidon
            reader_p, writer_p = await asyncio.open_connection(POSEIDON_HOST, POSEIDON_PORT)
            writer_p.write(b'\xfe')
            await writer_p.drain()
            response = await reader_p.read(1024)
            writer_p.close()
            await writer_p.wait_closed()

            motd = response.decode('utf-16be', errors='ignore').strip('\x00')
            parts = motd.split('\xa7')
            json_response = {
                "version": {"name": "1.7.3", "protocol": 39},
                "players": {"max": int(parts[1]) if len(parts) > 1 else 20, "online": 0},
                "description": {"text": parts[0] if parts else "Back2Beta Server"}
            }

            json_bytes = json.dumps(json_response).encode('utf-8')
            packet = write_varint(0x00) + write_varint(len(json_bytes)) + json_bytes
            full = write_varint(len(packet)) + packet
            writer.write(full)
            await writer.drain()
            print("[✓] Sent JSON MOTD")

            # Read ping packet and echo it
            ping_packet = await read_packet(reader)
            ping_id, ping_id_len = read_varint(ping_packet)
            if ping_id == 0x02:
                echo = write_varint(0x01) + ping_packet[ping_id_len:]
                full_echo = write_varint(len(echo)) + echo
                writer.write(full_echo)
                await writer.drain()
                print("[✓] Echoed ping packet")

            writer.close()
            await writer.wait_closed()
            return

        # Fallback: forward full connection to Poseidon
        reader_p, writer_p = await asyncio.open_connection(POSEIDON_HOST, POSEIDON_PORT)
        writer_p.write(write_varint(length) + handshake)
        await writer_p.drain()

        async def pipe(src, dst):
            try:
                while not src.at_eof():
                    data = await src.read(1024)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except:
                pass

        await asyncio.gather(
            pipe(reader, writer_p),
            pipe(reader_p, writer)
        )

    except Exception as e:
        print(f"[x] Error: {e}")
    finally:
        writer.close()
        await writer.wait_closed()

async def main():
    server = await asyncio.start_server(handle_client, '0.0.0.0', PROXY_PORT)
    print(f"[✓] Proxy listening on port {PROXY_PORT}")
    async with server:
        await server.serve_forever()

try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\n[!] Proxy interrupted by user. Shutting down.")
