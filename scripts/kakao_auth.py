"""카카오 최초 인증 헬퍼.

한 번만 실행해서 refresh_token을 얻는다. 이후에는 프로그램이 자동 갱신한다.

  python scripts/kakao_auth.py

사전 준비 (https://developers.kakao.com/console/app)
 1) 애플리케이션 추가 → REST API 키 확인
 2) [카카오 로그인] 활성화 ON
 3) [카카오 로그인] > Redirect URI 에 https://example.com/oauth 등록
    (실제 서버가 없어도 된다. 리다이렉트된 주소창의 code 값만 쓰면 된다)
 4) [카카오 로그인] > 동의항목 > '카카오톡 메시지 전송(talk_message)' 을
    **선택 동의**로 설정
"""
import sys
import urllib.parse

import requests

AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"


def main() -> int:
    rest_key = input("REST API 키: ").strip()
    redirect = input("Redirect URI [https://example.com/oauth]: ").strip() or "https://example.com/oauth"
    secret = input("Client Secret (미사용이면 엔터): ").strip()

    params = {
        "client_id": rest_key,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": "talk_message",   # 이 scope가 없으면 발송 시 403(-402)이 난다
    }
    print("\n1) 아래 주소를 브라우저에서 열고 동의하세요.\n")
    print(f"{AUTH_URL}?{urllib.parse.urlencode(params)}\n")
    print("2) 동의 후 이동한 주소의 ?code= 뒤 값을 복사하세요.")
    print("   (페이지가 열리지 않아도 주소창에는 code가 남아 있습니다)\n")

    code = input("code: ").strip()
    data = {
        "grant_type": "authorization_code",
        "client_id": rest_key,
        "redirect_uri": redirect,
        "code": code,
    }
    if secret:
        data["client_secret"] = secret

    r = requests.post(TOKEN_URL, data=data, timeout=15)
    if r.status_code != 200:
        print(f"\n실패 {r.status_code}: {r.text}")
        return 1

    tok = r.json()
    print("\n=== .env 또는 GitHub Secrets에 아래 값을 넣으세요 ===\n")
    print(f"KAKAO_REST_API_KEY={rest_key}")
    if secret:
        print(f"KAKAO_CLIENT_SECRET={secret}")
    print(f"KAKAO_ACCESS_TOKEN={tok.get('access_token')}")
    print(f"KAKAO_REFRESH_TOKEN={tok.get('refresh_token')}")
    print(f"\n(access_token 만료 {tok.get('expires_in')}초, "
          f"refresh_token 만료 {tok.get('refresh_token_expires_in')}초)")
    print("access_token은 자동 갱신되므로 refresh_token만 잘 보관하면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
