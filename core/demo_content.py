"""The restaurant's own opening checklist, as provided in "Checklist Mở Cửa Nhà Hàng" (Google Sheets).

Kept in the spreadsheet's Group<TAB>Item form so it goes through the same parser as a paste
in the checklist editor. Item names are exactly as in the sheet.
"""

OPENING_CHECKLIST_TITLE = "Mở cửa nhà hàng (Opening)"

OPENING_CHECKLIST = """\
Thiết bị & Vệ sinh\tKiểm tra tủ lạnh
\tKiểm tra máy rửa chén
\tXà bông rửa chén
\tXà bông rửa tay
\tKhăn giấy bàn
\tKhăn giấy lau tay
\tKhăn lau
Rau củ & Đồ tươi\tDưa leo
\tCà chua
\tHành lá
\tSalad
\tBơ (Avocado)
\tBroccoli luộc
\tBroccoli cắt sẵn
\tĐậu nành (Edamame)
Thịt, Hải sản & Protein\tCá tươi nig
\tCá tươi flame
\tCá đông lạnh
\tTôm Ebi (xả đông)
\tTôm 16/20
\tGà
\tĐậu hũ chiên (Tofu)
\tĐậu hũ Inari (Tofu nigiri Inari)
\tTrứng cuộn
Mì & Cơm\tNoodle pasta
\tNoodle rice
Các loại xốt (Sauce)\tSauce sesame salad
\tSauce yuzu salad
\tSauce soy noodle
\tSauce spicy noodle
\tSauce caramelize
\tSauce soy sushi
\tSauce soy rice bowl
\tSauce chili mayo
Gia vị & Đồ khô\tGochujang
\tSyrup
\tMuối (Salt)
\tĐường trắng (Sugar white)
\tĐường nâu (Sugar brown)
\tMSG (Bột ngọt)
\tOil spray
\tRong biển (Seaweed)
\tBột bắp
\tDầu mè
\tMirin
\tSoy box
\tVinegar box
\tLCK soy
\tSriracha
\tCá paste
\tTôm paste
Đồ uống & Khác\tTrà (Tea)
\tBột cà phê (Coffee powder)
\tSữa (Milk / Oat milk)
\tBình gas
Bao bì & Dụng cụ mang đi\tHộp H7
\tHộp H9
\tCốc cafe & nắp
\tTô súp & nắp
\tTúi mang đi (Bag)
\tHũ xốt & nắp (Soy cup & lid)
\tDĩa mang đi (Fork tw)
\tThìa mang đi (Spoon tw)
\tĐũa (Chopsticks)
"""

# Equipment checks are pass/fail; everything else is a stock level (enough / low).
PASS_FAIL_ITEMS = {"Kiểm tra tủ lạnh", "Kiểm tra máy rửa chén"}


def opening_tasks():
    from checklists.services import parse_task_lines

    return [
        {"section": group, "text": item, "required": True,
         "response_type": "PASS_FAIL" if item in PASS_FAIL_ITEMS else "STOCK_LEVEL"}
        for group, item in parse_task_lines(OPENING_CHECKLIST)
    ]
