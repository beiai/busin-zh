"""MIPS assembler used by the font patch."""

ZR, AT, V0, V1, A0, A1, A2, A3 = range(8)
T0, T1, T2, T3, T4, T5, T6, T7 = range(8, 16)
S0, S1 = 16, 17
SP, GP, RA = 29, 28, 31



class Asm:
    def __init__(self):
        self.words = []
        self.labels = {}
        self.fix = []

    def label(self, name):
        self.labels[name] = len(self.words)

    def emit(self, word):
        self.words.append(word & 0xFFFFFFFF)

    def r(self, rd, rs, rt, fn):
        self.emit((rs << 21) | (rt << 16) | (rd << 11) | fn)

    def i(self, op, rs, rt, imm):
        self.emit((op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF))

    def addiu(self, rt, rs, imm):
        self.i(9, rs, rt, imm)

    def lui(self, rt, imm):
        self.i(15, 0, rt, imm)

    def andi(self, rt, rs, imm):
        self.i(12, rs, rt, imm)

    def ori(self, rt, rs, imm):
        self.i(13, rs, rt, imm)

    def lh(self, rt, off, rs):
        self.i(33, rs, rt, off)

    def lbu(self, rt, off, rs):
        self.i(36, rs, rt, off)

    def lw(self, rt, off, rs):
        self.i(35, rs, rt, off)

    def sw(self, rt, off, rs):
        self.i(43, rs, rt, off)

    def sb(self, rt, off, rs):
        self.i(40, rs, rt, off)

    def sh(self, rt, off, rs):
        self.i(41, rs, rt, off)

    def ld(self, rt, off, rs):
        self.i(55, rs, rt, off)

    def sd(self, rt, off, rs):
        self.i(63, rs, rt, off)

    def sltiu(self, rt, rs, imm):
        self.i(11, rs, rt, imm)

    def sltu(self, rd, rs, rt):
        self.r(rd, rs, rt, 0x2B)

    def sll(self, rd, rt, sa):
        self.emit((rt << 16) | (rd << 11) | (sa << 6))

    def srl(self, rd, rt, sa):
        self.emit((rt << 16) | (rd << 11) | (sa << 6) | 2)

    def dsrl(self, rd, rt, sa):
        self.emit((rt << 16) | (rd << 11) | (sa << 6) | 0x3A)

    def addu(self, rd, rs, rt):
        self.r(rd, rs, rt, 33)

    def mult(self, rs, rt):
        self.emit((rs << 21) | (rt << 16) | 0x18)

    def mult_rd(self, rd, rs, rt):
        # R5900 mult rd, rs, rt writes the product to rd. rd 0 is the LO form.
        self.r(rd, rs, rt, 0x18)

    def mflo(self, rd):
        self.emit((rd << 11) | 0x12)

    def subu(self, rd, rs, rt):
        self.r(rd, rs, rt, 35)

    def j(self, va):
        self.emit(0x08000000 | ((va >> 2) & 0x03FFFFFF))

    def jal(self, va):
        self.emit(0x0C000000 | (va >> 2))

    def jalr(self, rs):
        self.emit((rs << 21) | (31 << 11) | 9)

    def jr(self, rs):
        self.emit((rs << 21) | 8)

    def nop(self):
        self.emit(0)

    def _br(self, op, rs, rt, name):
        self.fix.append((len(self.words), op, rs, rt, name))
        self.emit(0)

    def beq(self, rs, rt, name):
        self._br(4, rs, rt, name)

    def bne(self, rs, rt, name):
        self._br(5, rs, rt, name)

    def beql(self, rs, rt, name):
        self._br(20, rs, rt, name)

    def bnel(self, rs, rt, name):
        self._br(21, rs, rt, name)

    def done(self):
        for at, op, rs, rt, name in self.fix:
            off = self.labels[name] - (at + 1)
            if not -0x8000 <= off < 0x8000:
                raise ValueError(f"{name} is out of branch range")
            self.words[at] = (op << 26) | (rs << 21) | (rt << 16) | (off & 0xFFFF)
        return self.words


