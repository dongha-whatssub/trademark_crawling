import asyncio
from playwright.async_api import async_playwright
import json
import re
from datetime import datetime

# ================= 검색 및 제어 설정 =================
START_DATE = "20260211"
END_DATE = "20260212"
KEYWORD = f"RD=[{START_DATE}~{END_DATE}]"

# 기본 설정
GLOBAL_START_PAGE = 1
BATCH_SIZE = 400     # 500페이지 연속 넘길 시 웹에서 블락 명령, 접속 불가
SAVE_INTERVAL = 20

# 저장 파일명 설정
DATA_FILE = "kipris_test_data.jsonl"
ERROR_FILE = "kipris_errors.txt"
# ====================================================

# 시간 기록 함수
def get_now():
    return datetime.now().strftime("%H:%M:%S")

# 저장 로직을 함수로 분리하여 재사용
def save_data(data_list):
    if not data_list:
        return
    with open(DATA_FILE, "a", encoding="utf-8") as f:
        for r in data_list:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f" >> [SAVE] 데이터 저장 완료 (저장 건수: {len(data_list)}건)")

async def run_batch(start_p, end_p):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(no_viewport=True)

        # 불필요한 리소스 차단
        await context.route("**/*", lambda route: route.continue_() 
            if any(x in route.request.url for x in ["remoteFile.do", ".js", "ajax"]) 
            or route.request.resource_type in ["document", "script", "xhr", "fetch"] 
            else route.abort())

        page = await context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())

        try:
            print(f"\n🚀 [{get_now()}] 배치 시작: {start_p} ~ {end_p} 페이지 수집 개시")
            await page.goto("https://www.kipris.or.kr/khome/search/searchResult.do?tab=trademark", wait_until="domcontentloaded")
            
            await page.fill("#sd010301_g04_text", KEYWORD)
            await page.keyboard.press("Enter")
            await asyncio.sleep(7)

            # 필터 및 정렬 설정
            await page.evaluate("""() => {
                const allInput = document.querySelector('#allCheckMs');
                const regInput = document.querySelector('#measure08');
                const sel = document.querySelector('#sortCondition03');
                if (allInput && allInput.checked) document.querySelector('label[for="allCheckMs"]').click();
                if (regInput && !regInput.checked) document.querySelector('label[for="measure08"]').click();
                if (typeof filterSearch === 'function') filterSearch();
                setTimeout(() => {
                    if (sel) { sel.value = '90'; if (typeof optionSearch === 'function') optionSearch(); }
                }, 1000);
            }""")
            await asyncio.sleep(12)

            # 전체 페이지 수 계산
            try:
                total_page_text = await page.inner_text("span.totalPage", timeout=5000)
                total_pages = int(re.sub(r"[^\d]", "", total_page_text))
            except:
                print("전체 페이지 수를 가져오지 못했습니다. 기본값 설정.")
                total_pages = end_p

            actual_end_p = min(end_p, total_pages)

            # 시작 페이지로 이동
            await page.fill("#srchRsltPagingNum", str(start_p))
            await page.click("button.btn-jump")
            await asyncio.sleep(10)

            current_page = start_p
            temp_results = []

            while current_page <= actual_end_p:
                print(f"📄 [PAGE {current_page}/{total_pages}] 목록 수집 및 상세 진입 시작... {get_now()}")
                
                # 1. 목록 로딩 대기
                try:
                    await page.wait_for_selector("article.result_item", state="visible", timeout=20000)
                    await asyncio.sleep(1) 
                except Exception as e:
                    print(f"❌ P{current_page} 로딩 실패 (타임아웃): {e}")
                    with open(ERROR_FILE, "a", encoding="utf-8") as ef:
                        ef.write(f"P{current_page} 로딩 실패\n")
                    current_page += 1
                    try:
                        await page.fill("#srchRsltPagingNum", str(current_page))
                        await page.click("button.btn-jump")
                        await asyncio.sleep(5)
                    except: pass
                    continue

                items = page.locator("article.result_item")
                count = await items.count()
                print(f"   ㄴ 발견된 항목 수: {count}개")

                # ========================================================
                # 🔄 리스트 순회 시작 (상세 페이지 진입)
                # ========================================================
                for i in range(count):
                    try:
                        item = page.locator("article.result_item").nth(i)
                        
                        # 1. 기본 정보 추출 (목록 상에서)
                        title_el = item.locator("h1.title button.link.under").first
                        if await title_el.count() == 0: continue
                        title = (await title_el.inner_text()).strip()
                        
                        reg_el = item.locator(".head-title button.tit").first
                        reg_num = (await reg_el.inner_text()).strip() if await reg_el.count() > 0 else "N/A"

                        status_el = item.locator("span.badge[data-category='a']").first
                        status = (await status_el.inner_text()).strip() if await status_el.count() > 0 else "N/A"
                        
                        category_el = item.locator("li:has-text('상품분류') button.link").first
                        category = (await category_el.inner_text()).strip() if await category_el.count() > 0 else "N/A"
                        
                        img_el = item.locator("a.thumb img").first
                        img_src = await img_el.get_attribute("src") if await img_el.count() > 0 else None
                        img_url = f"https://www.kipris.or.kr{img_src}" if img_src else "N/A"

                        # 2. 🚀 상세 페이지(팝업) 열기
                        await title_el.click()
                        
                        # 3. 상세 영역 로딩 대기 (#mainResultDetailArea)
                        detail_area = page.locator("#mainResultDetailArea")
                        await detail_area.wait_for(state="visible", timeout=8000)
                        await asyncio.sleep(0.8) 

                        # 서지정보 + 지정상품 리스트 추출
                        all_basic_info = await detail_area.evaluate("""(area) => {
                            let result = {};
                            
                            // 1. 서지정보 등 기본 1:1 매칭 테이블 추출 (table-vert)
                            let vertRows = area.querySelectorAll('table.table-vert tbody tr');
                            vertRows.forEach(tr => {
                                let th = tr.querySelector('th');
                                let td = tr.querySelector('td');
                                if (th && td) {
                                    let key = th.textContent.trim().replace(/\s+/g, ' ');
                                    // 여러 줄로 된 내용은 ' | ' 기호로 구분
                                    let val = td.textContent.trim().replace(/\n/g, ' | ').replace(/\s{2,}/g, ' '); 
                                    if (key && !key.includes('법인번호') && !key.includes('사업자번호')) {
                                        result[key] = val;
                                    }
                                }
                            });

                            // 2. 지정상품 목록 완벽 추출 (#goodsList)
                            let goodsList = [];
                            // 화면에 안 보이는(더보기 안 눌러도) 숨겨진 row까지 textContent로 싹쓸이
                            let goodsRows = area.querySelectorAll('#goodsList tbody tr'); 
                            goodsRows.forEach(tr => {
                                let tds = tr.querySelectorAll('td');
                                // 번호, 상품분류, 유사군코드, 지정상품 4칸이 있는 정상적인 행일 경우
                                if (tds.length >= 4) {
                                    goodsList.push({
                                        "상품분류": tds[1].textContent.trim(),
                                        "유사군코드": tds[2].textContent.trim().replace(/\s+/g, ''),
                                        "지정상품명": tds[3].textContent.trim().replace(/\n/g, ' ')
                                    });
                                }
                            });
                            
                            // 지정상품이 하나라도 있으면 결과에 배열로 추가
                            if (goodsList.length > 0) {
                                result['지정상품목록'] = goodsList;
                            }

                            return result;
                        }""")

                        # 4. '최종권리자' 섹션 찾기 및 스크롤 (기존 로직 - 클릭 이벤트 유지)
                        rh_section = detail_area.locator(".tab-section-02").filter(has_text="최종권리자").first
                        owner_name, owner_addr, corp_num, biz_num = "N/A", "N/A", "N/A", "N/A"

                        if await rh_section.count() > 0:
                            await rh_section.scroll_into_view_if_needed()
                            row = rh_section.locator("tbody tr").first
                            
                            if await row.count() > 0:
                                owner_name = await row.locator("td").nth(0).evaluate("el => el.firstChild.textContent.trim()")
                                owner_addr = await row.locator("td").nth(1).inner_text()
                                
                                btn_corp = row.locator("button:has-text('법인번호')")
                                if await btn_corp.count() > 0:
                                    await btn_corp.click()
                                    await asyncio.sleep(0.3)
                                    corp_el = row.locator(".dropbox-con[data-dropbox*='trh'] p.txt, .dropbox-con[data-dropbox*='apn'] p.txt").first
                                    if await corp_el.count() > 0:
                                        corp_num = await corp_el.inner_text()

                                btn_biz = row.locator("button:has-text('사업자번호')")
                                if await btn_biz.count() > 0:
                                    await btn_biz.click()
                                    await asyncio.sleep(0.3)
                                    biz_el = row.locator("button:has-text('사업자번호') + .dropbox-con p.txt").first
                                    if await biz_el.count() > 0:
                                        biz_num = await biz_el.inner_text()

                        # 5. 🚪 상세 페이지 닫기
                        close_btn = page.locator("#btnCloseDetail")
                        if await close_btn.is_visible():
                            await close_btn.click()
                            await detail_area.wait_for(state="hidden", timeout=5000)
                        else:
                            await page.keyboard.press("Escape")
                            await asyncio.sleep(0.5)

                        # 🌟 6. 데이터 병합 (기존 추출 데이터 + 테이블 전체 긁은 데이터)
                        combined_data = {
                            "title": title, 
                            "reg_num": reg_num,
                            "status": status,
                            "category": category,
                            "owner_name_특정": owner_name,      
                            "owner_addr_특정": owner_addr,      
                            "corp_num": corp_num,          
                            "biz_num": biz_num,            
                            "img_url": img_url, 
                            "page": current_page
                        }
                        
                        # all_basic_info 딕셔너리에 담긴 수십 개의 상세 정보(출원인, 대리인, 일자 등)를 합칩니다.
                        combined_data.update(all_basic_info) 

                        temp_results.append(combined_data)

                        if (i + 1) % 5 == 0:
                            print(f"   -> {i + 1}번째 항목 상세 수집 완료...")

                    except Exception as e:
                        print(f"⚠️ 아이템 {i+1} 처리 중 오류 (건너뜀): {e}")
                        try:
                            await page.locator("#btnCloseDetail").click()
                            await page.locator("#mainResultDetailArea").wait_for(state="hidden", timeout=3000)
                        except:
                            await page.keyboard.press("Escape")
                        continue
                
                # === 리스트 순회 끝 ===

                save_data(temp_results)
                temp_results = []

                if current_page >= actual_end_p: break
                
                current_page += 1
                try:
                    await page.fill("#srchRsltPagingNum", str(current_page))
                    await page.click("button.btn-jump")
                    print(f"🏃 다음 페이지(P{current_page})로 이동 중...")
                    await asyncio.sleep(5) 
                except Exception as e:
                    print(f"❌ 페이지 이동 실패: {e}")
                    break

        finally:
            if 'temp_results' in locals() and temp_results:
                 print("🏁 배치 종료 전 잔여 데이터 저장")
                 save_data(temp_results)
            await browser.close()
            print(f"💤 배치 종료. 잠시 후 재시작합니다. {get_now()}")
            await asyncio.sleep(5)

async def main():
    current_start = GLOBAL_START_PAGE
    while True:
        current_end = current_start + BATCH_SIZE - 1
        try:
            total = await run_batch(current_start, current_end)
            if current_end >= total:
                print(f"🎉 모든 수집이 완료되었습니다! 총 {total}페이지 수집 완료 {get_now()}")
                break
            current_start += BATCH_SIZE
        except Exception as e:
            print(f"🔥 치명적 오류 발생, 10초 후 재시작: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())