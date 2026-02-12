import asyncio
from playwright.async_api import async_playwright
import json
import re

# ================= 검색 및 제어 설정 =================
START_DATE = "19500101"
END_DATE = "20260129"
KEYWORD = f"RD=[{START_DATE}~{END_DATE}]"

# 재개 페이지
GLOBAL_START_PAGE = 17311
BATCH_SIZE = 400     # 500페이지 연속 넘길 시 웹에서 블락 명령, 접속 불가
SAVE_INTERVAL = 10
DATA_FILE = "kipris_registered_data.jsonl"
ERROR_FILE = "kipris_errors.txt"
# ====================================================

# [추가] 저장 로직을 함수로 분리하여 재사용
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
            print(f"\n🚀 [배치 시작] {start_p} ~ {end_p} 페이지 수집 개시")
            await page.goto("https://www.kipris.or.kr/khome/search/searchResult.do?tab=trademark", wait_until="domcontentloaded") # networkidle 대신 변경 (속도 향상)
            
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
                print(f"[P {current_page}/{total_pages}] 추출 중...")
                
                # 1. 페이지 로딩 대기
                try:
                    await page.wait_for_selector("article.result_item, div.item-area", timeout=20000)
                    await asyncio.sleep(1) # 렌더링 안정화
                except Exception as e:
                    print(f"❌ P{current_page} 로딩 실패: {e}")
                    with open(ERROR_FILE, "a", encoding="utf-8") as ef:
                        ef.write(f"P{current_page} 로딩 실패\n")
                    
                    # [핵심 수정] 에러가 나서 다음 페이지로 넘어가기 전에, 지금까지 모은 데이터는 저장한다!
                    if temp_results:
                        print(f"⚠️ 에러 발생으로 인한 긴급 저장 (P{current_page})")
                        save_data(temp_results)
                        temp_results = [] # 비우기

                    current_page += 1
                    # 다음 페이지로 이동 시도 (이동 로직이 아래에 있으므로 여기서는 이동 처리 필요)
                    try:
                        await page.fill("#srchRsltPagingNum", str(current_page))
                        await page.click("button.btn-jump")
                        await asyncio.sleep(6)
                    except:
                        pass
                    continue

                # 2. 데이터 추출
                items = page.locator("article.result_item, div.item-area")
                count = await items.count()

                for i in range(count):
                    try:
                        item = items.nth(i)
                        
                        # 요소가 없을 때 에러 방지를 위한 로직 강화
                        title_el = item.locator("h1.title button.link.under").first
                        if await title_el.count() == 0: continue
                        title = (await title_el.inner_text()).strip()
                        
                        reg_el = item.locator(".head-title button.tit").first
                        reg_num = (await reg_el.inner_text()).strip() if await reg_el.count() > 0 else "N/A"
                        
                        applicant_el = item.locator("li:has-text('출원인') button.link").first
                        applicant = (await applicant_el.inner_text()).strip() if await applicant_el.count() > 0 else "N/A"
                        
                        owner_el = item.locator("li:has-text('최종권리자') button.link").first
                        owner = (await owner_el.inner_text()).strip() if await owner_el.count() > 0 else "N/A"

                        img_el = item.locator("a.thumb img").first
                        img_src = await img_el.get_attribute("src") if await img_el.count() > 0 else None
                        img_url = f"https://www.kipris.or.kr{img_src}" if img_src else "N/A"

                        temp_results.append({
                            "title": title, 
                            "reg_num": reg_num, 
                            "applicant": applicant,
                            "owner": owner, 
                            "img_url": img_url, 
                            "page": current_page
                        })
                    except Exception as e:
                        # 개별 아이템 추출 실패는 무시하고 계속 진행
                        continue

                # 3. 저장 로직 (SAVE_INTERVAL 마다 OR 마지막 페이지)
                if current_page % SAVE_INTERVAL == 0 or current_page == actual_end_p:
                    save_data(temp_results)
                    temp_results = [] # 저장 후 비우기

                # 4. 다음 페이지 이동
                if current_page >= actual_end_p: break
                
                current_page += 1
                try:
                    await page.fill("#srchRsltPagingNum", str(current_page))
                    await page.click("button.btn-jump")
                    await asyncio.sleep(5) # 대기 시간 소폭 최적화
                except Exception as e:
                    print(f"❌ 페이지 이동 중 에러: {e}")
                    # 이동 실패 시에도 일단 데이터 보호
                    if temp_results: save_data(temp_results)
                    temp_results = []

            return total_pages

        finally:
            # 배치 종료 시 혹시 남아있는 데이터가 있다면 저장
            if 'temp_results' in locals() and temp_results:
                 print("🏁 배치 종료 전 잔여 데이터 저장")
                 save_data(temp_results)
            await browser.close()
            print(f"💤 배치 종료. 잠시 후 재시작합니다.")
            await asyncio.sleep(5)

async def main():
    current_start = GLOBAL_START_PAGE
    while True:
        current_end = current_start + BATCH_SIZE - 1
        try:
            total = await run_batch(current_start, current_end)
            if current_end >= total:
                print("🎉 모든 수집이 완료되었습니다!")
                break
            current_start += BATCH_SIZE
        except Exception as e:
            print(f"🔥 치명적 오류 발생, 10초 후 재시작: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())