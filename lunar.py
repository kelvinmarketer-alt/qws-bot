"""
Lịch âm Việt Nam — thuật toán Hồ Ngọc Đức (timezone +7).
Nguồn gốc thuật toán: https://www.informatik.uni-leipzig.de/~duc/amlich/
Đây là chuẩn được dùng cho lịch âm VN, chính xác hơn lib lịch âm Trung Quốc
(vốn có thể lệch 1 ngày do khác múi giờ tính sóc/khí).
"""
import math

TIMEZONE = 7.0  # Việt Nam

CAN = ["Giáp", "Ất", "Bính", "Đinh", "Mậu", "Kỷ", "Canh", "Tân", "Nhâm", "Quý"]
CHI = ["Tý", "Sửu", "Dần", "Mão", "Thìn", "Tỵ", "Ngọ", "Mùi", "Thân", "Dậu", "Tuất", "Hợi"]

# Tên 12 trực thần (ngày hoàng đạo / hắc đạo) theo offset từ Thanh Long
DAY_OFFICERS = [
    ("Thanh Long", True), ("Minh Đường", True), ("Thiên Hình", False),
    ("Chu Tước", False), ("Kim Quỹ", True), ("Bảo Quang", True),
    ("Bạch Hổ", False), ("Ngọc Đường", True), ("Thiên Lao", False),
    ("Nguyên Vũ", False), ("Tư Mệnh", True), ("Câu Trận", False),
]


def jdFromDate(dd, mm, yy):
    a = int((14 - mm) / 12)
    y = yy + 4800 - a
    m = mm + 12 * a - 3
    jd = dd + int((153 * m + 2) / 5) + 365 * y + int(y / 4) - int(y / 100) + int(y / 400) - 32045
    if jd < 2299161:
        jd = dd + int((153 * m + 2) / 5) + 365 * y + int(y / 4) - 32083
    return jd


def jdToDate(jd):
    if jd > 2299160:
        a = jd + 32044
        b = int((4 * a + 3) / 146097)
        c = a - int((b * 146097) / 4)
    else:
        b = 0
        c = jd + 32082
    d = int((4 * c + 3) / 1461)
    e = c - int((1461 * d) / 4)
    m = int((5 * e + 2) / 153)
    day = e - int((153 * m + 2) / 5) + 1
    month = m + 3 - 12 * int(m / 10)
    year = b * 100 + d - 4800 + int(m / 10)
    return (day, month, year)


def NewMoon(k):
    T = k / 1236.85
    T2 = T * T
    T3 = T2 * T
    dr = math.pi / 180
    Jd1 = 2415020.75933 + 29.53058868 * k + 0.0001178 * T2 - 0.000000155 * T3
    Jd1 = Jd1 + 0.00033 * math.sin((166.56 + 132.87 * T - 0.009173 * T2) * dr)
    M = 359.2242 + 29.10535608 * k - 0.0000333 * T2 - 0.00000347 * T3
    Mpr = 306.0253 + 385.81691806 * k + 0.0107306 * T2 + 0.00001236 * T3
    F = 21.2964 + 390.67050646 * k - 0.0016528 * T2 - 0.00000239 * T3
    C1 = (0.1734 - 0.000393 * T) * math.sin(M * dr) + 0.0021 * math.sin(2 * dr * M)
    C1 = C1 - 0.4068 * math.sin(Mpr * dr) + 0.0161 * math.sin(dr * 2 * Mpr)
    C1 = C1 - 0.0004 * math.sin(dr * 3 * Mpr)
    C1 = C1 + 0.0104 * math.sin(dr * 2 * F) - 0.0051 * math.sin(dr * (M + Mpr))
    C1 = C1 - 0.0074 * math.sin(dr * (M - Mpr)) + 0.0004 * math.sin(dr * (2 * F + M))
    C1 = C1 - 0.0004 * math.sin(dr * (2 * F - M)) - 0.0006 * math.sin(dr * (2 * F + Mpr))
    C1 = C1 + 0.0010 * math.sin(dr * (2 * F - Mpr)) + 0.0005 * math.sin(dr * (2 * Mpr + M))
    if T < -11:
        deltat = 0.001 + 0.000839 * T + 0.0002261 * T2 - 0.00000845 * T3 - 0.000000081 * T * T3
    else:
        deltat = -0.000278 + 0.000265 * T + 0.000262 * T2
    return Jd1 + C1 - deltat


def SunLongitude(jdn):
    T = (jdn - 2451545.0) / 36525
    T2 = T * T
    dr = math.pi / 180
    M = 357.52910 + 35999.05030 * T - 0.0001559 * T2 - 0.00000048 * T * T2
    L0 = 280.46645 + 36000.76983 * T + 0.0003032 * T2
    DL = (1.914600 - 0.004817 * T - 0.000014 * T2) * math.sin(dr * M)
    DL = DL + (0.019993 - 0.000101 * T) * math.sin(dr * 2 * M) + 0.000290 * math.sin(dr * 3 * M)
    L = L0 + DL
    L = L * dr
    L = L - math.pi * 2 * int(L / (math.pi * 2))
    return L


def getNewMoonDay(k, timeZone):
    return int(NewMoon(k) + 0.5 + timeZone / 24)


def getSunLongitude(dayNumber, timeZone):
    return int(SunLongitude(dayNumber - 0.5 - timeZone / 24) / math.pi * 6)


def getLunarMonth11(yy, timeZone):
    off = jdFromDate(31, 12, yy) - 2415021
    k = int(off / 29.530588853)
    nm = getNewMoonDay(k, timeZone)
    sunLong = getSunLongitude(nm, timeZone)
    if sunLong >= 9:
        nm = getNewMoonDay(k - 1, timeZone)
    return nm


def getLeapMonthOffset(a11, timeZone):
    k = int((a11 - 2415021.076998695) / 29.530588853 + 0.5)
    i = 1
    arc = getSunLongitude(getNewMoonDay(k + i, timeZone), timeZone)
    while True:
        last = arc
        i += 1
        arc = getSunLongitude(getNewMoonDay(k + i, timeZone), timeZone)
        if not (arc != last and i < 14):
            break
    return i - 1


def solar2lunar(dd, mm, yy, timeZone=TIMEZONE):
    """Trả về (ngày_âm, tháng_âm, năm_âm, nhuận[0/1])."""
    dayNumber = jdFromDate(dd, mm, yy)
    k = int((dayNumber - 2415021.076998695) / 29.530588853)
    monthStart = getNewMoonDay(k + 1, timeZone)
    if monthStart > dayNumber:
        monthStart = getNewMoonDay(k, timeZone)
    a11 = getLunarMonth11(yy, timeZone)
    b11 = a11
    if a11 >= monthStart:
        lunarYear = yy
        a11 = getLunarMonth11(yy - 1, timeZone)
    else:
        lunarYear = yy + 1
        b11 = getLunarMonth11(yy + 1, timeZone)
    lunarDay = dayNumber - monthStart + 1
    diff = int((monthStart - a11) / 29)
    lunarLeap = 0
    lunarMonth = diff + 11
    if b11 - a11 > 365:
        leapMonthDiff = getLeapMonthOffset(a11, timeZone)
        if diff >= leapMonthDiff:
            lunarMonth = diff + 10
            if diff == leapMonthDiff:
                lunarLeap = 1
    if lunarMonth > 12:
        lunarMonth -= 12
    if lunarMonth >= 11 and diff < 4:
        lunarYear -= 1
    return (lunarDay, lunarMonth, lunarYear, lunarLeap)


def lunar2solar(lunarDay, lunarMonth, lunarYear, lunarLeap=0, timeZone=TIMEZONE):
    """Trả về (ngày_dương, tháng_dương, năm_dương) hoặc (0,0,0) nếu không hợp lệ."""
    if lunarMonth < 11:
        a11 = getLunarMonth11(lunarYear - 1, timeZone)
        b11 = getLunarMonth11(lunarYear, timeZone)
    else:
        a11 = getLunarMonth11(lunarYear, timeZone)
        b11 = getLunarMonth11(lunarYear + 1, timeZone)
    k = int(0.5 + (a11 - 2415021.076998695) / 29.530588853)
    off = lunarMonth - 11
    if off < 0:
        off += 12
    if b11 - a11 > 365:
        leapOff = getLeapMonthOffset(a11, timeZone)
        leapMonth = leapOff - 2
        if leapMonth < 0:
            leapMonth += 12
        if lunarLeap != 0 and lunarMonth != leapMonth:
            return (0, 0, 0)
        elif lunarLeap != 0 or off >= leapOff:
            off += 1
    monthStart = getNewMoonDay(k + off, timeZone)
    return jdToDate(monthStart + lunarDay - 1)


def can_chi_ngay(dd, mm, yy):
    jd = jdFromDate(dd, mm, yy)
    return "%s %s" % (CAN[(jd + 9) % 10], CHI[(jd + 1) % 12])


def can_chi_thang(lunarMonth, lunarYear):
    can = (lunarYear * 12 + lunarMonth + 3) % 10
    chi = (lunarMonth + 1) % 12
    return "%s %s" % (CAN[can], CHI[chi])


def can_chi_nam(lunarYear):
    return "%s %s" % (CAN[(lunarYear + 6) % 10], CHI[(lunarYear + 8) % 12])


def ngay_hoang_dao(dd, mm, yy, lunarMonth):
    """Xác định ngày hoàng đạo (tốt) / hắc đạo (xấu) + tên trực thần."""
    jd = jdFromDate(dd, mm, yy)
    day_chi = (jd + 1) % 12
    start_chi = ((lunarMonth - 1) % 6) * 2  # chi khởi Thanh Long theo tháng
    offset = (day_chi - start_chi) % 12
    name, good = DAY_OFFICERS[offset]
    return ("Hoàng đạo" if good else "Hắc đạo"), name, good
