using System;
using System.IO;
using System.Text;

namespace MultiSimCodec
{
    internal class HuffmanTable
    {
        public short[] count = new short[14];
        public short[] symbol = new short[256];
        public int[] encCode = new int[256];
        public int[] encLen = new int[256];
    }

    internal class BitReader
    {
        private byte[] data;
        private int pos, end, bitbuf, bitcnt;

        public BitReader(byte[] buf, int offset, int length)
        {
            data = buf; pos = offset; end = offset + length;
        }

        public int GetBits(int need)
        {
            int val = bitbuf;
            while (bitcnt < need)
            {
                if (pos >= end) throw new EndOfStreamException();
                val |= ((int)data[pos++]) << bitcnt;
                bitcnt += 8;
            }
            bitbuf = val >> need;
            bitcnt -= need;
            return val & ((1 << need) - 1);
        }

        public int Decode(HuffmanTable h)
        {
            int code = 0, first = 0, index = 0;
            for (int len = 1; len <= 13; len++)
            {
                code |= (GetBits(1) ^ 1);
                int cnt = h.count[len];
                if (code < first + cnt) return h.symbol[index + (code - first)];
                index += cnt; first = (first + cnt) << 1; code <<= 1;
            }
            throw new InvalidDataException();
        }
    }

    internal class BitWriter
    {
        private MemoryStream ms = new MemoryStream();
        private int bitbuf = 0, bitcnt = 0;

        public void PutBits(int val, int count)
        {
            bitbuf |= (val & ((1 << count) - 1)) << bitcnt;
            bitcnt += count;
            while (bitcnt >= 8)
            {
                ms.WriteByte((byte)(bitbuf & 0xFF));
                bitbuf >>= 8; bitcnt -= 8;
            }
        }

        public byte[] Finish()
        {
            if (bitcnt > 0) ms.WriteByte((byte)(bitbuf & 0xFF));
            return ms.ToArray();
        }
    }

    internal class Program
    {
        private const int BlockInfoSize = 8;
        private const int MaxBlockSize = 900000;
        private const string MagicSignature = "MSMCompressedElectronicsWorkbenchXML";

        private static readonly byte[] LitLen = new byte[] {
            11, 124, 8, 7, 28, 7, 188, 13, 76, 4, 10, 8, 12, 10, 12, 10, 8, 23, 8,
            9, 7, 6, 7, 8, 7, 6, 55, 8, 23, 24, 12, 11, 7, 9, 11, 12, 6, 7, 22, 5,
            7, 24, 6, 11, 9, 6, 7, 22, 7, 11, 38, 7, 9, 8, 25, 11, 8, 11, 9, 12,
            8, 12, 5, 38, 5, 38, 5, 11, 7, 5, 6, 21, 6, 10, 53, 8, 7, 24, 10, 27,
            44, 253, 253, 253, 252, 252, 252, 13, 12, 45, 12, 45, 12, 61, 12, 45,
            44, 173
        };
        private static readonly byte[] LenLen = new byte[] { 2, 35, 36, 53, 38, 23 };
        private static readonly byte[] DistLen = new byte[] { 2, 20, 53, 230, 247, 151, 248 };
        private static readonly short[] BaseLen = new short[] { 3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264 };
        private static readonly byte[] ExtraLen = new byte[] { 0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8 };

        private static HuffmanTable LitCode = new HuffmanTable();
        private static HuffmanTable LenCode = new HuffmanTable();
        private static HuffmanTable DistCode = new HuffmanTable();

        private static void ConstructTable(HuffmanTable h, byte[] rep)
        {
            short[] length = new short[256];
            int symbol = 0;
            for (int i = 0; i < rep.Length; i++)
            {
                int len = rep[i], left = (len >> 4) + 1;
                len &= 15;
                while (left-- > 0) length[symbol++] = (short)len;
            }
            int n = symbol;
            for (symbol = 0; symbol < n; symbol++) h.count[length[symbol]]++;
            short[] offs = new short[14];
            for (int len = 1; len < 13; len++) offs[len + 1] = (short)(offs[len] + h.count[len]);
            for (symbol = 0; symbol < n; symbol++)
                if (length[symbol] != 0) h.symbol[offs[length[symbol]]++] = (short)symbol;

            int first = 0, index = 0;
            for (int len = 1; len <= 13; len++)
            {
                int cnt = h.count[len];
                for (int k = 0; k < cnt; k++)
                {
                    int sym = h.symbol[index + k], c = first + k, streamBits = 0;
                    for (int b = 0; b < len; b++) streamBits |= ((((c >> (len - 1 - b)) & 1) ^ 1) << b);
                    h.encCode[sym] = streamBits; h.encLen[sym] = len;
                }
                index += cnt; first = (first + cnt) << 1;
            }
        }

        private static int ExplodeBlock(byte[] src, int srcOffset, int srcLen, byte[] dst, int dstOffset)
        {
            BitReader br = new BitReader(src, srcOffset, srcLen);
            int lit = br.GetBits(8), dict = br.GetBits(8), outPos = 0;
            while (true)
            {
                if (br.GetBits(1) != 0)
                {
                    int sym = br.Decode(LenCode);
                    int len = BaseLen[sym] + br.GetBits(ExtraLen[sym]);
                    if (len == 519) break;
                    int dBits = (len == 2) ? 2 : dict;
                    int dist = (br.Decode(DistCode) << dBits) + br.GetBits(dBits) + 1;
                    for (int i = 0; i < len; i++) { dst[dstOffset + outPos] = dst[dstOffset + outPos - dist]; outPos++; }
                }
                else
                {
                    dst[dstOffset + outPos++] = (byte)((lit != 0) ? br.Decode(LitCode) : br.GetBits(8));
                }
            }
            return outPos;
        }

        private static int FindLenSymbol(int len)
        {
            if (len == 2) return 1;
            if (len == 3) return 0;
            for (int s = 2; s < 16; s++)
                if (len >= BaseLen[s] && len <= BaseLen[s] + (1 << ExtraLen[s]) - 1) return s;
            return 15;
        }

        private static byte[] ImplodeBlock(byte[] src, int srcOffset, int srcLen)
        {
            BitWriter bw = new BitWriter();
            bw.PutBits(1, 8); bw.PutBits(6, 8);
            int[] head = new int[65536], prev = new int[srcLen];
            for (int i = 0; i < head.Length; i++) head[i] = -1;

            int pos = 0;
            while (pos < srcLen)
            {
                int bestLen = 0, bestDist = 0;
                if (pos + 2 < srcLen)
                {
                    int h = ((src[srcOffset + pos] << 8) ^ (src[srcOffset + pos + 1] << 4) ^ src[srcOffset + pos + 2]) & 0xFFFF;
                    int matchPos = head[h]; prev[pos] = matchPos; head[h] = pos;
                    int chainLen = 0, maxLen = Math.Min(518, srcLen - pos);
                    while (matchPos >= 0 && (pos - matchPos) <= 4096 && chainLen++ < 48)
                    {
                        if (src[srcOffset + matchPos + bestLen] == src[srcOffset + pos + bestLen])
                        {
                            int l = 0;
                            while (l < maxLen && src[srcOffset + matchPos + l] == src[srcOffset + pos + l]) l++;
                            if (l >= 3 && l > bestLen) { bestLen = l; bestDist = pos - matchPos; if (l >= 128) break; }
                        }
                        matchPos = prev[matchPos];
                    }
                }
                if (bestLen >= 3)
                {
                    bw.PutBits(1, 1);
                    int lSym = FindLenSymbol(bestLen);
                    bw.PutBits(LenCode.encCode[lSym], LenCode.encLen[lSym]);
                    if (ExtraLen[lSym] > 0) bw.PutBits(bestLen - BaseLen[lSym], ExtraLen[lSym]);
                    int d = bestDist - 1, dSym = d >> 6, dExtra = d & 0x3F;
                    bw.PutBits(DistCode.encCode[dSym], DistCode.encLen[dSym]);
                    bw.PutBits(dExtra, 6);
                    for (int k = 1; k < bestLen; k++)
                    {
                        int p = pos + k;
                        if (p + 2 < srcLen)
                        {
                            int hk = ((src[srcOffset + p] << 8) ^ (src[srcOffset + p + 1] << 4) ^ src[srcOffset + p + 2]) & 0xFFFF;
                            prev[p] = head[hk]; head[hk] = p;
                        }
                    }
                    pos += bestLen;
                }
                else
                {
                    bw.PutBits(0, 1);
                    int b = src[srcOffset + pos++];
                    bw.PutBits(LitCode.encCode[b], LitCode.encLen[b]);
                }
            }
            bw.PutBits(1, 1);
            bw.PutBits(LenCode.encCode[15], LenCode.encLen[15]);
            bw.PutBits(0xFF, 8);
            return bw.Finish();
        }

        static void Main(string[] args)
        {
            if (args == null || args.Length < 3) Environment.Exit(1);
            ConstructTable(LitCode, LitLen);
            ConstructTable(LenCode, LenLen);
            ConstructTable(DistCode, DistLen);
            byte[] fileData = File.ReadAllBytes(args[1]);
            if (args[0].ToLowerInvariant() == "decode")
            {
                uint totalUncompSize = BitConverter.ToUInt32(fileData, 36);
                byte[] outputBuffer = new byte[totalUncompSize];
                int pos = 36 + BlockInfoSize, bytesWritten = 0;
                while (pos < fileData.Length)
                {
                    uint blkComp = BitConverter.ToUInt32(fileData, pos + 4);
                    bytesWritten += ExplodeBlock(fileData, pos + BlockInfoSize, (int)blkComp, outputBuffer, bytesWritten);
                    pos += (int)blkComp + BlockInfoSize;
                }
                File.WriteAllBytes(args[2], outputBuffer);
            }
            else
            {
                byte[] sigBytes = Encoding.ASCII.GetBytes(MagicSignature);
                using (FileStream fs = File.Create(args[2]))
                {
                    fs.Write(sigBytes, 0, sigBytes.Length);
                    fs.Write(BitConverter.GetBytes((uint)fileData.Length), 0, 4);
                    fs.Write(BitConverter.GetBytes((uint)0), 0, 4);
                    int bytesRead = 0;
                    while (bytesRead < fileData.Length)
                    {
                        int blockSize = Math.Min(fileData.Length - bytesRead, MaxBlockSize);
                        byte[] compBlock = ImplodeBlock(fileData, bytesRead, blockSize);
                        fs.Write(BitConverter.GetBytes((uint)blockSize), 0, 4);
                        fs.Write(BitConverter.GetBytes((uint)compBlock.Length), 0, 4);
                        fs.Write(compBlock, 0, compBlock.Length);
                        bytesRead += blockSize;
                    }
                }
            }
        }
    }
}
